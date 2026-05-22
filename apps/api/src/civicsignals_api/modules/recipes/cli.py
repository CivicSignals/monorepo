"""Recipe authoring + regression CLI (doc 18 §3, §3.3; TODO D1, D5, QA-4).

Subcommands::

    # Validate a recipe YAML against the canonical JSON Schema.
    python -m civicsignals_api.modules.recipes.cli validate recipes/wa-state-webs/recipe.yml

    # Replay golden fixtures (the deterministic CI gate) — all or one recipe.
    python -m civicsignals_api.modules.recipes.cli test
    python -m civicsignals_api.modules.recipes.cli test wa-state-webs

    # Dry-run a recipe against a local HTML file or a live URL (fetch+extract),
    # printing extracted fields + any `degraded` flag (doc 18 §2.3, §3.4).
    python -m civicsignals_api.modules.recipes.cli preview --recipe wa-state-webs --html page.html
    python -m civicsignals_api.modules.recipes.cli preview --recipe-file r.yml --url https://…

    # Scaffold a starter recipe under recipes/<id>/.
    python -m civicsignals_api.modules.recipes.cli scaffold my-new-source

Exit code is non-zero on a validation failure, a fixture drift, or a failed
preview extraction — so ``test`` wires straight into CI (QA-4).

Backward-compat: invoking the module with bare recipe ids (no subcommand) — e.g.
``... cli`` or ``... cli wa-state-webs`` — still runs the fixture replay, the D1
behavior CI depends on.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import services
from .runner import RecipeError
from .schemas import FixtureReplayResult, PreviewResult

_SUBCOMMANDS = frozenset({"validate", "test", "preview", "scaffold"})


# ---------------------------------------------------------------------------
# test (golden-fixture replay) — the D1 CI gate
# ---------------------------------------------------------------------------


def _cmd_test(recipe_ids: Sequence[str]) -> int:
    ids = list(recipe_ids) if recipe_ids else services.list_recipe_ids()
    if not ids:
        print("no recipes found under recipes/", file=sys.stderr)
        return 1

    failures = 0
    for recipe_id in ids:
        try:
            results = services.replay_recipe(recipe_id)
        except RecipeError as exc:
            print(f"FAIL {recipe_id}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for result in results:
            failures += _report_replay(result)
    if failures:
        print(f"\n{failures} fixture(s) failed", file=sys.stderr)
        return 1
    print(f"\nall fixtures passed across {len(ids)} recipe(s)")
    return 0


def _report_replay(result: FixtureReplayResult) -> int:
    if result.passed:
        print(f"PASS {result.recipe_id} :: {result.fixture}")
        return 0
    print(f"FAIL {result.recipe_id} :: {result.fixture}", file=sys.stderr)
    if result.diff:
        print(result.diff, file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _cmd_validate(paths: Sequence[str]) -> int:
    failures = 0
    for path in paths:
        try:
            recipe = services.load_recipe_file(path)
        except services.RecipeValidationError as exc:
            print(f"INVALID {path}", file=sys.stderr)
            for message in exc.messages:
                print(f"  - {message}", file=sys.stderr)
            failures += 1
            continue
        except RecipeError as exc:
            print(f"ERROR {path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        print(f"OK {path} (recipe_id={recipe.recipe_id}, version={recipe.version})")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        recipe = _load_preview_recipe(args)
    except RecipeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        if args.html is not None:
            html = Path(args.html).read_text(encoding="utf-8")
            result = services.preview_html(recipe, html, source_url=f"file://{args.html}")
        else:
            with services.HttpxFetcher() as fetcher:
                result = services.preview_url(recipe, args.url, fetcher)
    except OSError as exc:
        print(f"ERROR: cannot read {args.html}: {exc}", file=sys.stderr)
        return 1
    except RecipeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        _print_preview(result)
    return 0 if result.ok else 1


def _load_preview_recipe(args: argparse.Namespace) -> services.Recipe:
    if args.recipe is not None:
        return services.load_recipe(args.recipe)
    return services.load_recipe_file(args.recipe_file)


def _print_preview(result: PreviewResult) -> None:
    status = "OK" if result.ok else "FAILED"
    flags = " degraded" if result.degraded else ""
    print(f"{status} {result.recipe_id} v{result.recipe_version} <- {result.source}{flags}")
    print(f"  signal_types: {', '.join(result.signal_types) or '(none)'}")
    print("  fields:")
    for field in result.fields:
        mark = "[x]" if field.matched else "[ ]"
        req = " (required)" if field.required else ""
        miss = "  <- MISSING REQUIRED" if field.missing_required else ""
        rendered = repr(field.value) if field.value is not None else "(no match)"
        print(f"    {mark} {field.name}{req}: {rendered}{miss}")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def _cmd_scaffold(args: argparse.Namespace) -> int:
    try:
        if args.stdout:
            print(services.render_recipe(args.recipe_id, connector=args.connector))
            return 0
        path = services.scaffold_recipe(
            args.recipe_id,
            connector=args.connector,
            overwrite=args.force,
        )
    except RecipeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"scaffolded {args.recipe_id} -> {path}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recipes",
        description="Author, validate, preview, and regression-test recipes (doc 18 §3).",
    )
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate recipe YAML against the schema")
    p_validate.add_argument("paths", nargs="+", help="recipe.yml path(s) to validate")

    p_test = sub.add_parser("test", help="replay golden fixtures (CI gate)")
    p_test.add_argument("recipe_ids", nargs="*", help="recipe id(s); default: all")

    p_preview = sub.add_parser("preview", help="dry-run a recipe against HTML or a URL")
    recipe_src = p_preview.add_mutually_exclusive_group(required=True)
    recipe_src.add_argument("--recipe", help="recipe id under recipes/")
    recipe_src.add_argument("--recipe-file", help="path to a recipe.yml")
    sample = p_preview.add_mutually_exclusive_group(required=True)
    sample.add_argument("--html", help="path to a local HTML file to extract from")
    sample.add_argument("--url", help="URL to fetch (honors robots/politeness) then extract")
    p_preview.add_argument("--json", action="store_true", help="emit the full result as JSON")

    p_scaffold = sub.add_parser("scaffold", help="write a starter recipe skeleton")
    p_scaffold.add_argument("recipe_id", help="new recipe id (lowercase, [a-z0-9_-])")
    p_scaffold.add_argument(
        "--connector",
        default="http_static",
        help="connector to instantiate (default: http_static)",
    )
    p_scaffold.add_argument("--force", action="store_true", help="overwrite an existing recipe")
    p_scaffold.add_argument(
        "--stdout", action="store_true", help="print the YAML instead of writing a file"
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Handle --help/-h explicitly before the backward-compat check so they are
    # not silently treated as recipe ids and passed to fixture replay.
    if args and args[0] in ("-h", "--help"):
        _build_parser().print_help()
        return 0

    # Backward-compat (D1): no subcommand -> fixture replay over the given ids.
    if not args or args[0] not in _SUBCOMMANDS:
        return _cmd_test(args)

    namespace = _build_parser().parse_args(args)
    if namespace.command == "validate":
        return _cmd_validate(namespace.paths)
    if namespace.command == "test":
        return _cmd_test(namespace.recipe_ids)
    if namespace.command == "preview":
        return _cmd_preview(namespace)
    if namespace.command == "scaffold":
        return _cmd_scaffold(namespace)
    return _cmd_test([])  # pragma: no cover - unreachable (subparser dispatch)


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
