export const meta = {
  name: 'build-course-broker',
  description: "Answer Universal Academy's model calls with Claude Code agents while the app builds a course",
  whenToUse: 'Started by /build-course while the app runs with UA_AGENT_DIR. args: {dir, backend} (absolute paths).',
  phases: [{ title: 'Answer', detail: "claim the app's requests, one agent per model call" }],
}

// The app, started with UA_AGENT_DIR=<dir>, writes every model call as a request file and waits for its answer
// (backend/app/agent_llm.py). This broker claims the requests (`python -m app.agent_llm claim`, broker mode) and has one
// agent answer each. It stops when the app writes <dir>/DONE, when the app has waited on nothing for about 20 minutes
// after work was done (the build is over), or after about 30 minutes in which no request came (no build was started).
// Optional args: parallel (answer agents at once, default 5: keep it below the workflow's agent cap so a claim can
// always run), wait (seconds one claim waits for work, default 420; the Bash tool allows 600).

if (!args || !args.dir || !args.backend) throw new Error('args {dir, backend} are required, as absolute paths')
const DIR = args.dir
const BACKEND = args.backend
const PY = BACKEND + '/.venv/bin/python'
const PARALLEL = args.parallel || 5
const WAIT = args.wait || 420
const IDLE_ROUNDS = 3 // ~21 minutes with nothing to do after work was done: the build is over
const NEVER_ROUNDS = 4 // ~28 minutes without a single request: no build was started
const MAX_ATTEMPTS = 3 // agents per request before giving up on it (the app then times the call out)
const MAX_AGENTS = 980 // a workflow may start 1000 agents; stop claiming before that
const q = (s) => '"' + s + '"' // paths may contain spaces

const CLAIM = {
  type: 'object',
  properties: {
    done: { type: 'boolean' },
    requests: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, role: { type: 'string' }, label: { type: 'string' }, effort: { type: 'string' }, chars: { type: 'number' } },
        required: ['id', 'role', 'label'],
      },
    },
    waiting: { type: 'array', items: { type: 'string' } },
  },
  required: ['done', 'requests'],
}

const claimPrompt = (max, held, skip) =>
  'Run exactly this one command with the Bash tool (timeout 600000 ms), then return the JSON object it prints as ' +
  'structured output. Do nothing else.\n\n' +
  'cd ' + q(BACKEND) + ' && ' + q(PY) + ' -m app.agent_llm claim ' + q(DIR) + ' --wait ' + WAIT + ' --max ' + max +
  ' --held' + held.map((id) => ' ' + id).join('') + (skip.length ? ' --skip' + skip.map((id) => ' ' + id).join('') : '')

const answerPrompt = (r) => {
  const req = DIR + '/requests/' + r.id
  return [
    'You stand in for the API model in ONE call of the Universal Academy course pipeline (the app would otherwise send ' +
      'this call to the OpenAI or Anthropic API). Your answer goes through the same schema check, reviews and ' +
      'verification as an API answer.',
    '',
    'Request ' + r.id + ' (role ' + r.role + ': ' + r.label + ') is in ' + req + '/:',
    '- instructions.md: the system prompt. Follow every rule in it.',
    '- context.md (if present): shared context, such as the chapter text.',
    '- message.md: the task.',
    '- schema.json: the answer schema. Its field descriptions are instructions too.',
    'Images attached to the call (page renders, figures) are listed under "images" in ' + req + '.json: look at each ' +
      'one with the Read tool.',
    '',
    'Do the work yourself, at the quality the instructions ask for. Check algebra and computations with SymPy (' +
      q(PY) + ' has it) when useful.',
    'Work in few tool calls: every call re-reads this whole conversation, so read each file once and in full (one Read ' +
      'per file; page through it only if the Read result says it was cut), never re-read a file, write the answer with ' +
      'one Python script, and after a failed validation fix only what the error names.',
    'Write ONLY the JSON object of your answer to ' + DIR + '/responses/' + r.id + '.json.tmp, produced with ' +
      "Python's json.dump (it gets LaTeX backslashes right). Then run",
    '  cd ' + q(BACKEND) + ' && ' + q(PY) + ' -m app.agent_llm validate ' + q(DIR) + ' ' + r.id,
    'and fix the answer until it prints OK.',
    '',
    "Safety: change no other file; never read any .env file; never open the app's data folder (its database and the " +
      "learner's progress); never run anything in backend/scripts; never start a server; call no external API.",
    'Final message: OK ' + r.id + ', or one line saying why you could not answer.',
  ].join('\n')
}

// The effort the app asked for (2026-10-11: a fixed effort per role ran every planner and review at xhigh — 3–8x slower
// than the API, which uses high for non-proof skills and for a proof's final review); the role's level only when the
// request names none. OpenAI's 'minimal' has no agent equivalent: low.
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
const ROLE_EFFORT = { graph: 'high', lesson: 'medium', verify: 'high', vision: 'low', viz: 'low', topup: 'medium' }
const effortFor = (r) => (r.effort === 'minimal' ? 'low' : EFFORTS.includes(r.effort) ? r.effort : ROLE_EFFORT[r.role] || 'high')
// The model the app's role names, as on the API: Haiku reads pages and checks answers; Opus builds the skill map and
// plans, writes, reviews and repairs lessons, visuals and practice questions — whatever model this session runs.
const ROLE_MODEL = { verify: 'haiku', vision: 'haiku' }
const modelFor = (r) => ROLE_MODEL[r.role] || 'opus'

phase('Answer')
const running = new Map() // request id -> its agent (a promise that removes itself when the agent ends)
const attempts = {}
const gaveUp = new Set()
let spawned = 0
let dispatched = 0
let answered = 0
let idle = 0
let claimFailures = 0
let waiting = []
let stoppedBecause = ''

const answer = (r) =>
  agent(answerPrompt(r), { label: r.role + ' ' + r.label, model: modelFor(r), effort: effortFor(r) })
    .then((out) => {
      if (typeof out === 'string' && /^\s*OK\b/.test(out)) answered++
      return out
    }, () => null)
    .finally(() => running.delete(r.id))

for (let round = 0; ; round++) {
  if (running.size >= PARALLEL) {
    await Promise.race(running.values())
    continue
  }
  if (spawned >= MAX_AGENTS) {
    stoppedBecause = 'this workflow used its agent allowance: run /build-course again to go on'
    break
  }
  spawned++
  const got = await agent(claimPrompt(PARALLEL - running.size, [...running.keys()], [...gaveUp]), {
    label: 'claim #' + round,
    schema: CLAIM,
    model: 'haiku',
    effort: 'low',
  })
  if (!got) {
    if (++claimFailures >= 3) {
      stoppedBecause = 'the claim command failed three times in a row'
      break
    }
    continue
  }
  claimFailures = 0
  if (got.done) {
    stoppedBecause = 'the app wrote DONE'
    break
  }
  let started = 0
  for (const r of got.requests) {
    if (running.has(r.id) || gaveUp.has(r.id)) continue
    attempts[r.id] = (attempts[r.id] || 0) + 1
    if (attempts[r.id] > MAX_ATTEMPTS) {
      gaveUp.add(r.id)
      log('Gave up on ' + r.role + ' ' + r.label + ' (' + r.id + ') after ' + MAX_ATTEMPTS + ' agents; the app will report that call as failed.')
      continue
    }
    spawned++
    dispatched++
    started++
    running.set(r.id, answer(r))
  }
  if (started) log(answered + ' of ' + dispatched + ' calls answered, ' + running.size + ' agents working')
  waiting = (got.waiting || []).filter((id) => !gaveUp.has(id))
  idle = started === 0 && running.size === 0 && waiting.length === 0 ? idle + 1 : 0
  if (dispatched === 0 && idle >= NEVER_ROUNDS) {
    stoppedBecause = 'no request came from the app'
    log(
      'No request came from the app in ' + Math.round((NEVER_ROUNDS * WAIT) / 60) + ' minutes. Start a build in the app ' +
        '(Library: open your book, pick a chapter, Build course), then run /build-course again.',
    )
    break
  }
  if (dispatched > 0 && idle >= IDLE_ROUNDS) {
    stoppedBecause = 'the app has had nothing to answer for ' + Math.round((IDLE_ROUNDS * WAIT) / 60) + ' minutes'
    break
  }
}

await Promise.all(running.values())
return { stoppedBecause, dispatched, answered, gaveUp: [...gaveUp], stillWaiting: waiting }
