# Customer module delivery

## Files and exports

- `src/customer/account.tsx`: `CustomerAuthForm({mode,embedded,onSuccess})`; `AccountLoginPage`, `AccountRegisterPage`, `AccountVerifyPage`, `AccountForgotPage`, `AccountResetPage`, `AccountPage`, `AccountSecurityPage`, `RewardsPage`, `InvitePage`, `OrderLinkPage`; reusable `OrderImportButton({orderId,onSuccess?})` for order details.
- `src/customer/account.css`: responsive warm account/auth/coupon/invitation layout, existing Button/Panel/Field/Notice/Modal components retained.
- `src/customer/account/security.ts`: fragment clearing, pending exchange single-flight, invitee privacy guard, coupon status grouping, local customer return-target validation.
- `tests/customer-account.test.ts`: 6 security-boundary tests.

## Implemented behavior

- Auth forms have visible/hidden passwords, confirmation, optional invitation code, required terms/privacy acknowledgement, guest continuation and DEMO-only explicit account button. Optional `returnTo`/`next` returns only to a local `/u` route. Embedded callbacks preserve checkout ownership of navigation.
- Registration remains a pending challenge stored only in memory. Resend has cooldown; pending-email correction requires `registrationChallengeId`. Mail UI says queued only for `mailStatus:QUEUED`; missing queue acknowledgement reports unconfirmed availability and guest fallback. Server mail failures remain visible and retryable.
- Verify/reset read and immediately clear `#token`. Both require explicit submit to POST, with no automatic mail-token consumption. `?mode=emailchange` and `?mode=email-change` use confirm-email-change. Password/token are never persisted.
- Successful customer login/verification prompts for explicit guest linking, with skip. Linking POST requires `confirm:true`, checks `rotationCompleted:true` and explains revoked old guest session and new guest principal. Customer logout uses only identity.logoutCustomer.
- Profile, password, request-email-change and notification forms invoke growth APIs. Security page reads devices, supports per-session and other-session revocation with confirmation.
- Rewards display available/reserved/used/inactive tabs; frozen is independent, with service, threshold, expiry, reason, and reserved order link.
- Invite reads code/share text/rules and privacy-filtered referral list plus totals. No static reward amount promise.
- OrderLink immediately clears fragment and automatically exchanges only order authorization. A ref plus module pending-operation map prevents StrictMode duplicate exchange; identity generation is checked. It stores only the returned matching order in store. Anonymous/non-import users proceed to details; verified ORDER_IMPORT users can explicitly add just this order or continue. Existing matching in-memory access reopens a tokenless link. There is no phone-based private lookup.
- `OrderImportButton` provides the same explicit second-confirmation affordance for existing order details; it does not link guest history or transfer ownership.

## Shared interface assumptions

- `useIdentity` from `src/growth/identity.tsx`; customer is CustomerSession, orderAccess is singular OrderAccess|null.
- `growthRequest`, `useGrowthResource` from `src/growth/client.ts`; hook handles cache clearing and stale identity responses.
- GET account returns CustomerAccount directly; sessions/coupons return `{items}`; referrals returns `{items,total,qualified,granted}`; referral code returns `{code,shareText,rules}`.
- Registration result `{registrationChallengeId,emailMasked,mailStatus,retryAfterSeconds}`; resend/forgot/request-email-change use the same mail acknowledgement subset. Production mail-unavailable should throw.
- Profile PATCH `{displayName,expectedVersion}`; preferences PATCH `{orderCreated,orderStatusUpdated,referralReward,expectedVersion}`.
- Change password `{currentPassword,newPassword}`; reset `{token,newPassword}`; request email change `{newEmail,currentPassword,expectedVersion}`; mail verify/confirm `{token}`.
- Sessions revoke `{sessionId}` or `{sessionId:"ALL_OTHERS"}`; import `{orderId,confirm:true}`; exchange `{orderId,accessToken}` -> `{order,access}`.

## Verification and remaining integration

- `npm test -- tests/customer-account.test.ts`: **6 passed**.
- `npx tsc --noEmit --pretty false`: **passed** after contract/StrictMode follow-up; shared blockers present during initial implementation have been resolved by the primary agent.
- No browser, visual screenshot, real mail delivery, or real backend/CSRF check was performed, per assignment. Parent must register routes and may wire `OrderImportButton` into existing order details. Shared files were not edited.

### Contract and StrictMode follow-up

- Order exchange sends `{orderId,accessToken:token}`; revoke-other-sessions sends `{sessionId:"ALL_OTHERS"}`.
- Fragment initialization now uses pure `readFragmentToken`. `useLayoutEffect` clears the fragment at commit before paint, after both StrictMode initializer calls; the retained secret stays in component memory. Mail verification/reset POST remains exclusively in explicit form submission. The order-access exchange alone is automatic, with its existing once-ref and pending promise deduplication.
- Regression test simulates double pure initialization and repeated fragment clearing, asserting the captured secret remains available. This is a helper-level regression test, not a browser/React runtime test.
