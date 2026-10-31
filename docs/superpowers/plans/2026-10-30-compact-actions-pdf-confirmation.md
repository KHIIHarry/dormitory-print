# Compact actions and final PDF confirmation

Goal: compact admin operations, make the entire QR upload area clickable, require confirmation inside the final PDF preview, and hand off the exact current flow to backend development.

- Preserve existing staff permissions, pending guards, operation titles, optimistic versions, and confirmation dialogs. Use existing Lucide SVG icons in a single nonwrapping row with accessible labels and hover titles.
- Use an actual button for the entire QR dashed area and a hidden file input; retain preview, file validation, upload progress and existing public-asset purpose. Do not add an icon upload button.
- Split final PDF preview into VIEW and CONFIRM intent. Checking an unconfirmed task opens CONFIRM without changing the checkbox. Only a rendered, current artifact can be confirmed. Closing or failing preview leaves it unchecked; the separate view action never has a confirmation button. Unchecking removes the acknowledgment.
- Keep existing hash/revision invalidation, quote acknowledgment and backend previewAcks contract. Update existing flow tests for the new confirmation step.
- Update both backend Prompts with a self-contained current-flow supplement and OpenAPI references, including image paper choice, original/A4 asset paths, 30-character customer note, asynchronous operations, and confirmation boundaries.
- Verify with unit tests, production build, actual browser customer/admin flows, responsive screenshots, and a scoped diff review. No backend deployment or integration claim.

Accepted steering completed: the QR region also accepts a single dropped image; its WeChat mark uses the user's original SVG unchanged. Individual device exits use SVG, bulk exit stays textual. Both backend Prompts and login OpenAPI descriptions now specify a 2-device account limit, refusing a third new device without evicting existing sessions.

Verification: 160 unit tests passed; production build and diff checks passed; 38 E2E cases collected (not claimed executed). Actual browser checks and limitations are recorded in docs/qa/2026-10-30-compact-actions-final-preview.md.
