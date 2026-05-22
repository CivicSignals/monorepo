# Prompts

Versioned LLM prompts (doc 18 §3.5). A recipe references a prompt by name +
version; a bad prompt is rolled back by changing the version pointer, with no
code deploy. Prompt registry tooling lands in TODO E3.

Layout (per E3):

```
prompts/
  <prompt_name>/
    v1.txt
    v2.txt
```
