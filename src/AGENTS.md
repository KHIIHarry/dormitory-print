# Frontend UI/UX changes

Read `../DESIGN.md` and `../docs/UIUX-移动端布局规范.md` before changing frontend layouts or native controls. Preserve existing product behavior and concise Chinese copy.

- Date/month/time/datetime-local controls use `Field` and the shared native-input reset in styles.css. Do not create page-specific workarounds that omit other native types.
- Keep Flex/Grid children shrinkable; measure against the parent content box, including padding. Never hide page overflow at html/body/app root to conceal an oversized child.
- Mobile: normal Chinese letter spacing, 16px editable controls, >=44px targets, complete currency amounts, and explicit toolbar wrapping.
- For native inputs or mobile layout changes, run `npm run test:e2e:mobile-ui` (Chromium + WebKit), inspect mobile/desktop screenshots, then run relevant unit tests and build. First install the WebKit browser with `npm run test:e2e:install-webkit` if missing.
- Report real verification scope. Chromium touch emulation is not Safari, and desktop WebKit emulation is not a physical iPhone. Do not claim real-device acceptance without actually testing one.
