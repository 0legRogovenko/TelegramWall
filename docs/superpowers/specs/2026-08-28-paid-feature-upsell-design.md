# Paid Feature Upsell Design

## Goal

Increase awareness of TelegramWall's paid features without turning channel
delivery into advertising spam. Free users who actively use the bot receive a
standalone, localized offer at most once every three days. Existing contextual
offers remain visible when a user reaches a Free limit or requests a paid
feature.

## Scope

The feature applies only to users whose current `subscription_tier` is `free`.
Administrators and users with an active Basic or Pro subscription never receive
scheduled upsell messages.

A user is eligible for scheduled upsell only when all of the following are true:

- a language has been selected;
- at least one `UserChannel` is active;
- the account was created at least three days ago;
- no upsell was successfully delivered during the previous three days.

The feature does not add third-party advertising, change subscription prices,
change delivery timing, or append advertisements to forwarded channel posts and
AI digests.

## User Experience

The bot sends a separate Telegram message after the three-day cooldown. The
offer rotates deterministically between three themes so that repeated messages
do not use identical copy:

1. Basic: summarize long posts on demand.
2. Pro: receive auto-summaries and a daily AI digest instead of notification
   duplication.
3. Capacity: monitor up to the Basic limit or unlimited channels on Pro.

All variants use the user's selected language: Russian, English, or Spanish.
The message contains current prices obtained from the existing payment module,
so marketing copy cannot drift from invoice amounts.

If the user has not used the trial, the first inline action offers the existing
three-day Pro trial. Basic and Pro purchase buttons remain available below it.
If the trial was already used, only paid-plan actions are shown.

Contextual messages shown when a user reaches a channel limit or requests an
unavailable AI feature remain immediate because they explain why the requested
action cannot continue. A contextual offer also advances the scheduled upsell
cooldown, preventing a second generic promotion shortly afterward. The same
rule applies to a successfully delivered subscription-expired notice because it
already contains a purchase action. A pre-expiry warning does not need this
update: the user is still paid and therefore ineligible for scheduled upsells.

## Architecture

### Persistence

Add nullable `User.upsell_last_sent_at` using an additive migration. A null value
means no successful upsell has been recorded. The account `created_at` value is
the initial cooldown anchor for new users.

No campaign counter is stored. The variant is selected from a stable combination
of the user ID and the current three-day period. This keeps rotation deterministic
without another migration.

### Upsell service

Create `src/services/upsell.py` with small, independently testable operations:

- select a bounded batch of eligible users;
- choose the localized campaign variant;
- build the appropriate inline keyboard;
- send an offer;
- record the timestamp only after Telegram accepts the message.

Database work runs through `asyncio.to_thread`. Telegram failures are logged and
leave `upsell_last_sent_at` unchanged, allowing a later retry. One failed chat
does not prevent delivery to other eligible users.

### Scheduler

Start an upsell loop alongside the existing monitor background tasks. It runs
once after startup and then every hour. Each pass handles at most 50 users to
avoid a deploy causing a large Telegram burst. Remaining eligible users are
picked up on later passes.

The business cooldown is three days and is configurable through
`UPSELL_INTERVAL_DAYS`, defaulting to `3`. The batch size is configurable through
`UPSELL_BATCH_SIZE`, defaulting to `50`.

### Contextual offers

Existing subscription keyboards remain the single path into invoices. A shared
helper records a contextual upsell after its Telegram message succeeds. This
helper is used by the channel-limit, unavailable-AI, and subscription-expired
responses touched by this change. It must never suppress the explanatory
response itself.

### Metrics and reporting

Add an `upsell_sent` metric for successful scheduled and contextual offers. The
daily admin report includes the number of offers sent during the reporting
window. Payment conversion remains represented by existing active-subscription
and payment data; this change does not introduce attribution analytics.

## Failure and Concurrency Behaviour

- A Telegram send failure does not consume the cooldown.
- A database failure is logged and ends only the current pass.
- The bot marks an offer after successful send, not before it.
- The deployment uses one bot process and one serial scheduler loop. Eligibility
  is revalidated immediately before each send so a user who subscribed after
  batch selection is skipped.
- All HTML values originating outside static translations are escaped.

## Testing

Tests cover:

- new accounts are ineligible for the first three days;
- users without active channels are ineligible;
- active paid users and administrators are excluded;
- eligible Free users are selected after the cooldown;
- a successful send records the timestamp;
- a failed send leaves the timestamp unchanged;
- trial and paid keyboards differ correctly;
- campaign variants render in Russian, English, and Spanish;
- the 50-user batch limit is respected;
- the daily report counts successful upsells.

The complete repository test suite, flake8, and `git diff --check` must pass
before the implementation commit is pushed to `main`. Deployment is verified by
CI, the long-running Bot workflow step, and a fresh external heartbeat.
