---
name: actualizar-memoria
description: Usar al cerrar una tarea relevante, tomar una decisión, aplicar una migración o cambiar un comando, para registrar lo aprendido en docs/memory/, CLAUDE.md y las skills dentro del mismo PR.
---

# Actualizar memoria, CLAUDE.md y skills

La memoria compartida vive en el repo, no solo en la máquina de una persona.

## Cuándo
Al cerrar cada tarea relevante, y siempre que:
- se tome una decisión (por qué se eligió A y no B);
- se aplique algo en el servidor o en Supabase (migración, tarea programada, cuenta nueva);
- cambie un comando, script, variable o nombre que aparece en README, `docs/` o skills;
- Oscar o Daniel den una instrucción de trabajo nueva ("siempre haz X").

## Cómo
1. Escribir `docs/memory/<slug-en-minusculas>.md` con este frontmatter:
   ```
   ---
   name: <slug>
   description: <una línea: qué hecho guarda y para qué sirve>
   metadata:
     type: project | reference | user | feedback
   ---
   ```
   Cuerpo breve: el hecho, el **por qué**, y cómo aplicarlo. Fechas absolutas (AAAA-MM-DD).
2. Añadir una línea en `docs/memory/MEMORY.md`: `- [Título](slug.md) — descripción corta`.
3. Si cambia una regla de trabajo o de convivencia: actualizar `CLAUDE.md`.
4. Si cambia un comando: revisar `.claude/skills/*/SKILL.md`, `README.md` y `docs/`.
5. Si un trabajo se repite 2 o más veces: proponer una skill nueva en `.claude/skills/<nombre>/SKILL.md`
   (frontmatter `name` y `description` que diga CUÁNDO usarla).
6. Incluir todo en el mismo PR que el cambio.

## Qué NO guardar
- Conclusiones de exp-2 (no son confiables).
- Secretos (claves, contraseñas).
- Lo que ya se ve leyendo el código o el historial de git.

## Espejo local
El almacén de Claude de cada máquina (`~/.claude/projects/.../memory/`) es solo un espejo.
Ante divergencia gana `docs/memory/`.
