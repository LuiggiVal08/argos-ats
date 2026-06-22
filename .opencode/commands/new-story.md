---
description: Redacta una nueva Historia de Usuario siguiendo el formato de spec.md sección 5
agent: plan
---

You are drafting a new user story for the argos-ats project, in the same style as @spec.md section 5.

Brief from the user: $ARGUMENTS

Re-read @spec.md section 5 to mirror the format exactly:
- Title with épica grouping
- "Como / Quiero / Para" block in Spanish
- Bulleted acceptance criteria split into **Happy Path** and **Sad Path**
- Reference the relevant hexagonal layer (Domain / Application / Infrastructure) the work belongs to
- Cross-link to any existing story it depends on (e.g. "depends on Historia 2")

Constraints to respect:
- English-or-Spanish code identifiers, but the prose narrative must be in Spanish to match the spec.
- Cite the specific spec section (e.g. section 1.3, section 4) that the story implements.
- Flag any spec ambiguity the user should resolve.

Output the new story as a copy-pasteable markdown block, ready to append to @spec.md. Do not edit any files.
