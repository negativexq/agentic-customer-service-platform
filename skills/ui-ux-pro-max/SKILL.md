---
name: ui-ux-pro-max
description: Project-local guidance for refining the Agentic Customer Service Operator Console into an accessible enterprise AI operations platform.
---

# UI/UX Pro Max — Operator Console

This project-local skill is the design-system contract for future Operator Console work.

## Product direction

Design for an enterprise AI infrastructure platform used by support operators, engineers, and evaluators. The interface should feel deliberate, dense, calm, and trustworthy, with the information hierarchy and interaction quality of Linear, Vercel, Datadog, Stripe internal tools, and OpenAI platform interfaces.

Prioritize:

- clear run-level hierarchy and progressive disclosure;
- reusable, typed components instead of one-off markup;
- semantic status communication;
- keyboard and screen-reader usability;
- desktop-first responsive layouts that remain usable on smaller laptops;
- restrained transitions that explain state changes;
- safe presentation of agent metadata without chain-of-thought or sensitive payloads.

Avoid:

- generic consumer chatbot styling;
- template dashboard grids made of disconnected cards;
- excessive gradients, glow, or decorative motion;
- inconsistent spacing and arbitrary typography;
- equal visual weight for primary and secondary information;
- emoji as interface icons;
- exposing prompts, model completions, raw arguments, private customer fields, or retrieved document bodies.

## Implementation rules

- Keep design primitives in `frontend/src/components/ui/` when they are shared by two or more features.
- Keep feature-specific composition in `frontend/src/components/` or a feature folder; do not put domain logic in primitives.
- Use the existing React + TypeScript + Vite + Tailwind stack unless a dependency is clearly necessary and approved.
- Keep props explicit and typed. Prefer semantic HTML and visible focus states.
- Use icons from one consistent icon library if icons are needed; never substitute emojis.
- Validate every refinement with `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build`.
- Do not modify backend APIs or behavior as part of a frontend UX task.
