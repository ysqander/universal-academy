# Security

Universal Academy is a local, single-user app. It has no login and no user accounts.

- **Network:** `./run.sh` binds to `127.0.0.1` by default, so only your own computer can reach the app.
  `UA_HOST=0.0.0.0 ./run.sh` exposes it to your whole network **without authentication**: anyone who can reach the port
  can read your courses, use your API keys through the app (which costs you money) and add or delete books. Do this
  only on a network you trust, and never on a public one or directly on the internet. Importing a book by file path is
  limited to the machine that runs the app; uploads work from anywhere the app can be reached.
- **API keys:** keys are kept on your machine, in `.env` at the project root or in `<data folder>/api-keys.json`
  (file mode 600) when you enter them in the app (header key icon → **API keys**). The app never sends a key back to
  the browser; it shows only whether a key is set and its last 4 characters. Keep `.env` out of git (it is in
  `.gitignore`).
- **Model providers:** building a course sends the chapter's page images and text to the provider you configured
  (OpenAI, Anthropic, NVIDIA), or to Claude Code on the Claude Code route.

## Reporting a problem

Open an issue on this repository's GitHub issue tracker and describe the problem and how to reproduce it. Do not post
API keys, personal data or an exploit that works against other people's running instances in a public issue. In that
case, open an issue that asks for a private contact, without the details, or use GitHub's private vulnerability
reporting if it is enabled for this repository.
