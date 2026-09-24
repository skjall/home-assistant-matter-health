// Colours, spacing and type shared by every component.
//
// The page follows Home Assistant's light or dark mode when it can read it,
// otherwise the system setting. Each role of a finding's chain has its own
// colour so the story reads at a glance: amber for why, rose for what
// happened, violet for what it means, green for what to do.

import { css } from "lit";

export const tokens = css`
  :host {
    --mh-bg: #f6f7fb;
    --mh-surface: #ffffff;
    --mh-surface-2: #f1f3f8;
    --mh-border: #e2e6ef;
    --mh-text: #161a23;
    --mh-muted: #586173;
    --mh-primary: #0a6cd1;
    --mh-primary-soft: #e6f0fc;
    --mh-cause: #9a4a06;
    --mh-cause-soft: #fff4e0;
    --mh-cause-dot: #f59e0b;
    --mh-effect: #b4123b;
    --mh-effect-soft: #fff0f3;
    --mh-effect-dot: #f43f5e;
    --mh-impact: #5b21b6;
    --mh-impact-soft: #f4f0ff;
    --mh-impact-dot: #8b5cf6;
    --mh-fix: #03684a;
    --mh-fix-soft: #e7f8f0;
    --mh-fix-dot: #10b981;
    --mh-problem: #b91c1c;
    --mh-warning: #a15c07;
    --mh-info: #0a6cd1;
    --mh-ok: #047857;
    --mh-shadow: 0 1px 2px rgb(16 24 40 / 6%), 0 4px 16px rgb(16 24 40 / 6%);
    --mh-radius: 16px;
    --mh-font: "Inter", "Roboto", system-ui, -apple-system, "Segoe UI", sans-serif;
    color: var(--mh-text);
    font-family: var(--mh-font);
    -webkit-font-smoothing: antialiased;
  }

  :host([theme="dark"]) {
    --mh-bg: #111318;
    --mh-surface: #1a1d24;
    --mh-surface-2: #22262f;
    --mh-border: #2e333e;
    --mh-text: #eef0f5;
    --mh-muted: #a3abbc;
    --mh-primary: #6db3ff;
    --mh-primary-soft: #16263a;
    --mh-cause: #fcd07a;
    --mh-cause-soft: #2b2112;
    --mh-effect: #ff9fb2;
    --mh-effect-soft: #2d1519;
    --mh-impact: #c9b5ff;
    --mh-impact-soft: #211b33;
    --mh-fix: #6ee7b7;
    --mh-fix-soft: #11271f;
    --mh-problem: #ff8a8a;
    --mh-warning: #fcd07a;
    --mh-info: #6db3ff;
    --mh-ok: #6ee7b7;
    --mh-shadow: 0 1px 2px rgb(0 0 0 / 30%), 0 6px 20px rgb(0 0 0 / 25%);
  }
`;

export const base = css`
  * {
    box-sizing: border-box;
  }
  button {
    font: inherit;
    color: inherit;
  }
  svg.icon {
    width: 20px;
    height: 20px;
    fill: currentColor;
    flex: none;
  }
  .muted {
    color: var(--mh-muted);
  }
  .card {
    background: var(--mh-surface);
    border: 1px solid var(--mh-border);
    border-radius: var(--mh-radius);
    box-shadow: var(--mh-shadow);
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    line-height: 20px;
    white-space: nowrap;
  }
`;
