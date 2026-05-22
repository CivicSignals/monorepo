# @civicsignals/recipe-schema

The canonical JSON Schemas for **connector descriptors** and **recipes**
(doc 16 §17, doc 18 §3). One source of truth, consumed by:

- the Python recipe runner / validator (`packages/sdk-py`, TODO D1),
- recipe authoring tooling and CI fixture validation (TODO D5, A3, QA-4),
- editor schema hints for community recipe authors.

```js
import { recipeSchema, connectorSchema } from "@civicsignals/recipe-schema";
```

Schemas live in `schema/`. A **connector** is code we write per source type; a
**recipe** is config that instantiates a connector for one tenant/source.
