"""Offline proof-track fixtures for the fake LLM (UA_FAKE_LLM=1) and the tests: a proof plan and a lesson that follow
THE PROOF TRACK (app/prompts/<provider>/proof.py) — the S4a design ("Prove complex arithmetic laws by comparing coordinates") with
every key checked in SymPy. The lesson passes every check of pipeline/proof_track.py; tests break it on purpose."""

from __future__ import annotations

from .fake_llm import _block, _check, _marker, _multistep, _question, _step
from .llm_schemas import (
    KnowledgePointDraft,
    LessonV2Draft,
    NotationItem,
    PracticeStatement,
    ProofPlan,
    QuestionDraft,
    ScopeStatement,
    WorkedExample,
    WorkedStep,
)

SLOT_HEADER = "| product | $x$ | $y$ | $u$ | $v$ | real $xu-yv$ | imaginary $xv+yu$ |\n|---|---|---|---|---|---|---|\n"


def proof_plan() -> ProofPlan:
    return ProofPlan(
        pattern="two_routes",
        strategy_name="Two routes, same destination",
        statement="$(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for all $\\alpha,\\beta,\\lambda\\in\\mathbf{C}$ (Theorem 1.3)",
        picture="Route A multiplies α and β, then multiplies by λ; route B multiplies β and λ, then multiplies α by that; "
        "the law says the routes always meet.",
        may_use=["Definition 1.1: $(a+bi)(c+di)=(ac-bd)+(ad+bc)i$", "equal complex numbers have equal coordinates",
                 "the laws of the real numbers"],
        may_not_use=["the law itself", "expanding complex brackets with $i^2=-1$ — that is complex distributivity, "
                     "another law of 1.3"],
        letters=["α = a+bi", "β = c+di", "λ = g+hi"],
        coordinate_letters=["a", "b", "c", "d", "g", "h"],
        numbers="α = 1+i, β = 2+i, λ = i: route A gives 1+3i, then −3+i; route B gives −1+2i, then −3+i.",
        guided_case="λ = i, α = a+bi, β = c+di: the learner builds route B's last leg α(βi) = (−ad−bc)+(ac−bd)i",
        guided_key="-a*d-b*c+(a*c-b*d)*i",
        general_case="λ = g+hi, as one route table",
        scope_statements=[
            ScopeStatement(statement="$(\\alpha\\beta)i=\\alpha(\\beta i)$ for every $\\alpha,\\beta$", true=True),
            ScopeStatement(statement="$(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for every $\\alpha,\\beta,\\lambda$", true=False),
            ScopeStatement(statement="the calculation with $\\alpha=1+i$, $\\beta=2+i$ proved it for every $\\alpha,\\beta$", true=False),
        ],
        practice_statements=[
            PracticeStatement(statement="$(1+i)(\\alpha+\\beta)=(1+i)\\alpha+(1+i)\\beta$", knowledge_point=1, role="special_case"),
            PracticeStatement(statement="$(\\alpha+\\beta)(2-i)=\\alpha(2-i)+\\beta(2-i)$", knowledge_point=1, role="special_case"),
            PracticeStatement(statement="$(1-i)(\\alpha+\\beta)=(1-i)\\alpha+(1-i)\\beta$", knowledge_point=1, role="repair"),
            PracticeStatement(statement="$(1+i)(\\alpha\\beta)=((1+i)\\alpha)((1+i)\\beta)$ (false)", knowledge_point=2, role="decide"),
        ],
        capstone_statement="$(\\alpha\\beta)\\mu=(\\alpha\\mu)\\beta$ with $\\mu=(2-i)(1+i)$; then $\\alpha\\beta=2-5i$ gives $(\\alpha\\mu)\\beta$",
        halves=[],
    )


def _blocks() -> list:
    return [
        _block(
            "setup", "Two routes, same destination",
            "Theorem 1.3 says $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for all $\\alpha,\\beta,\\lambda\\in\\mathbf{C}$. "
            "Route A multiplies $\\alpha$ and $\\beta$, then multiplies the result by $\\lambda$. Route B multiplies $\\beta$ "
            "and $\\lambda$, then multiplies $\\alpha$ by that. The law says the routes always end at the same number; we may "
            "not assume it — it is what we prove.\n\n"
            "- We may use: Definition 1.1, $(a+bi)(c+di)=(ac-bd)+(ad+bc)i$; equal complex numbers have equal coordinates; "
            "the laws of real numbers.\n"
            "- We may not use: the law itself, or expanding complex brackets with $i^2=-1$ — that relies on complex "
            "distributivity, another law of 1.3.",
        ),
        _block(
            "demonstrate", "First, fill the four slots",
            "Before every product, write the factors as $x+yi$ and $u+vi$ and fill four slots: the real part is $xu-yv$ and "
            "the imaginary part is $xv+yu$. Take $\\alpha=1+i$, $\\beta=2+i$, $\\lambda=i=0+1i$. Route B starts with "
            "$\\beta\\lambda$.\n\n" + SLOT_HEADER + "| $\\beta\\lambda=(2+i)(0+1i)$ | $2$ | $1$ | $0$ | $1$ | $-1$ | $2$ |\n\n"
            "So $\\beta\\lambda=-1+2i$.",
        ),
        _block(
            "check", "Finish route B", "Route B now multiplies $\\alpha$ by $\\beta\\lambda$.",
            check=_check(kind="expression",
                         prompt="Reset the slots — $x,y$ from $\\alpha=1+i$; $u,v$ from $\\beta\\lambda=-1+2i$ — and enter "
                         "$\\alpha(\\beta\\lambda)$ as a complex number in standard form (e.g. $3-2i$).",
                         answer="-3+i", answer_display="$-3+i$",
                         explanation="Slots $1,1,-1,2$: real part $1\\cdot(-1)-1\\cdot 2=-3$, imaginary part $1\\cdot 2+1\\cdot(-1)=1$."),
        ),
        _block(
            "check", "Route A on the same numbers",
            "Route A, with fresh slots:\n\n" + SLOT_HEADER
            + "| $\\alpha\\beta=(1+i)(2+i)$ | $1$ | $1$ | $2$ | $1$ | $1$ | $3$ |\n"
            "| $(\\alpha\\beta)\\lambda=(1+3i)(0+1i)$ | $1$ | $3$ | $0$ | $1$ | $-3$ | $1$ |",
            check=_check(kind="reflect",
                         prompt="Both routes reached $-3+i$. Why does this not yet prove $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$?",
                         answer_display="It checks one choice of the three numbers; the law claims every choice, so we need "
                         "inputs that stand for every number at once.",
                         explanation="One example shows the law for one choice of inputs only."),
        ),
        _block(
            "demonstrate", "Letters stand for every input",
            "Keep $\\lambda=i$ and write $\\alpha=a+bi$, $\\beta=c+di$, where $a,b,c,d$ are any real numbers: one calculation "
            "then covers every $\\alpha$ and $\\beta$. Route B starts with $\\beta i$.\n\n" + SLOT_HEADER
            + "| $\\beta i=(c+di)(0+1i)$ | $c$ | $d$ | $0$ | $1$ | $-d$ | $c$ |\n\nSo $\\beta i=-d+ci$.",
        ),
        _block(
            "faded_example", "Your turn: route B's last product",
            "Route B now multiplies $\\alpha$ by $\\beta i$. Reset the slots.\n\n" + SLOT_HEADER
            + "| $\\alpha(\\beta i)=(a+bi)(-d+ci)$ | $a$ | $b$ | ? | ? | ? | ? |",
            check=_check(kind="expression",
                         prompt="Enter $\\alpha(\\beta i)$ as a complex number in standard form (e.g. $3-2i$); the "
                         "coordinates may stay unexpanded.",
                         answer="-a*d-b*c+(a*c-b*d)*i", answer_display="$(-ad-bc)+(ac-bd)i$",
                         explanation="$u=-d$ and $v=c$: real part $a(-d)-bc$, imaginary part $ac+b(-d)$."),
        ),
        _block(
            "demonstrate", "Route A, then compare",
            "Route A, slot by slot:\n\n" + SLOT_HEADER
            + "| $\\alpha\\beta$ | $a$ | $b$ | $c$ | $d$ | $ac-bd$ | $ad+bc$ |\n"
            "| $(\\alpha\\beta)i$ | $ac-bd$ | $ad+bc$ | $0$ | $1$ | $-(ad+bc)$ | $ac-bd$ |\n\n"
            "| coordinate | route A | route B | why equal |\n|---|---|---|---|\n"
            "| real | $-(ad+bc)$ | $-ad-bc$ | real distributivity |\n| imaginary | $ac-bd$ | $ac-bd$ | identical |\n\n"
            "Both coordinates agree for every $a,b,c,d$, so $(\\alpha\\beta)i=\\alpha(\\beta i)$ for all $\\alpha,\\beta\\in\\mathbf{C}$.",
        ),
        _block(
            "check", "What has been proved?", "Before the general case, say exactly what the letters covered.",
            check=_check(kind="true_false_set", prompt="What has been proved so far? Mark each statement true or false.",
                         choices=["$(\\alpha\\beta)i=\\alpha(\\beta i)$ for every $\\alpha,\\beta\\in\\mathbf{C}$",
                                  "$(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for every $\\alpha,\\beta,\\lambda\\in\\mathbf{C}$",
                                  "The calculation with $\\alpha=1+i$, $\\beta=2+i$ proved $(\\alpha\\beta)i=\\alpha(\\beta i)$ for every $\\alpha,\\beta$"],
                         answer="TFF", answer_display="True, false, false",
                         explanation="The letters covered every $\\alpha,\\beta$ with $\\lambda=i$ fixed; the numbers covered one case."),
        ),
        _block(
            "generalize", "Every λ: one more pair of letters",
            "The same steps, with $\\lambda=g+hi$:\n\n"
            "| | route A: $(\\alpha\\beta)\\lambda$ | route B: $\\alpha(\\beta\\lambda)$ |\n|---|---|---|\n"
            "| inner product | $\\alpha\\beta=(ac-bd)+(ad+bc)i$ | $\\beta\\lambda=(cg-dh)+(ch+dg)i$ |\n"
            "| slots of the outer product | $ac-bd,\\ ad+bc,\\ g,\\ h$ | $a,\\ b,\\ cg-dh,\\ ch+dg$ |\n"
            "| real coordinate | $acg-bdg-adh-bch$ | $acg-bdg-adh-bch$ |\n"
            "| imaginary coordinate | $ach-bdh+adg+bcg$ | $ach-bdh+adg+bcg$ |\n\n"
            "Real distributivity expands each entry, and reordering real products and sums puts the same four terms in the "
            "same order. The letters were arbitrary, so $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for all "
            "$\\alpha,\\beta,\\lambda\\in\\mathbf{C}$. The method: name the two routes, compute each from Definition 1.1 "
            "filling the slots before every product, and compare both coordinates using only real-number laws.",
        ),
    ]


def _transfer_point() -> KnowledgePointDraft:
    statement = (
        "Strategy: two routes, same destination. Prove $(1+i)(\\alpha+\\beta)=(1+i)\\alpha+(1+i)\\beta$ for all "
        "$\\alpha=a+bi$, $\\beta=c+di$ ($a,b,c,d$ real). Route A adds first, then multiplies by $1+i$; route B multiplies "
        "each by $1+i$, then adds. The four slots of $(x+yi)(u+vi)$ are $x,y,u,v$; real part $xu-yv$, imaginary part $xv+yu$."
    )
    walkthrough = _multistep(
        "k1q1", statement,
        [
            _step("expression", "Route A: enter $(1+i)(\\alpha+\\beta)$ as a complex number in standard form (e.g. $3-2i$); "
                  "the coordinates may stay unexpanded.", answer="a+c-b-d+(a+b+c+d)*i",
                  display="$(a+c-b-d)+(a+b+c+d)i$", hint="$\\alpha+\\beta=(a+c)+(b+d)i$: the slots are $1,1,a+c,b+d$.",
                  explanation="Real part $1(a+c)-1(b+d)$, imaginary part $1(b+d)+1(a+c)$."),
            _step("expression", "Route B starts with $(1+i)\\alpha$. Reset the slots and enter $(1+i)\\alpha$ in standard form.",
                  answer="a-b+(a+b)*i", display="$(a-b)+(a+b)i$", hint="The slots are $1,1,a,b$.",
                  explanation="In the same way $(1+i)\\beta=(c-d)+(c+d)i$, so route B ends at $(a-b+c-d)+(a+b+c+d)i$."),
            _step("multiple_choice", "Route A's imaginary coordinate is $(b+d)+(a+c)$; route B's is $(a+b)+(c+d)$. Which "
                  "fact about real numbers makes them equal?",
                  choices=["Real products distribute over real sums", "Real sums can be regrouped and reordered",
                           "Real products can be reordered", "Adding $0$ changes nothing"],
                  correct=1, display="Real sums can be regrouped and reordered",
                  hint="Both sides add the same four letters.",
                  explanation="The real coordinates agree too, by distributing the minus sign and regrouping, so the law "
                  "holds for all $\\alpha,\\beta$."),
        ],
        tier=2, display="$(1+i)(\\alpha+\\beta)=(1+i)\\alpha+(1+i)\\beta$",
        solution="Route A gives $(a+c-b-d)+(a+b+c+d)i$; route B gives $(a-b)+(a+b)i$ plus $(c-d)+(c+d)i$; the coordinates "
        "agree by regrouping real sums.",
        targets="transfer: prove a distributive law with two routes",
    )
    recovery = _multistep(
        "k1q2",
        "Strategy: two routes, same destination. Prove $(\\alpha+\\beta)(2-i)=\\alpha(2-i)+\\beta(2-i)$ for all "
        "$\\alpha=a+bi$, $\\beta=c+di$ ($a,b,c,d$ real). Route A adds first, then multiplies by $2-i$; route B multiplies "
        "each by $2-i$, then adds.",
        [
            _step("expression", "Route A: enter $(\\alpha+\\beta)(2-i)$ in standard form (e.g. $3-2i$); the coordinates may "
                  "stay unexpanded.", answer="2*a+b+2*c+d+(2*b-a+2*d-c)*i", display="$(2a+b+2c+d)+(2b-a+2d-c)i$",
                  hint="The slots are $a+c,b+d,2,-1$.", explanation="Real $2(a+c)+(b+d)$, imaginary $-(a+c)+2(b+d)$."),
            _step("expression", "Route B starts with $\\alpha(2-i)$. Reset the slots and enter it in standard form.",
                  answer="2*a+b+(2*b-a)*i", display="$(2a+b)+(2b-a)i$", hint="The slots are $a,b,2,-1$.",
                  explanation="With $\\beta(2-i)=(2c+d)+(2d-c)i$ route B meets route A."),
            _step("true_false_set", "Mark each statement about this proof true or false.",
                  choices=["Both coordinates agree for every $a,b,c,d$, so the law holds for all $\\alpha,\\beta$",
                           "Comparing the real coordinates alone would prove it",
                           "It also proves $(\\alpha+\\beta)\\lambda=\\alpha\\lambda+\\beta\\lambda$ for every $\\lambda$"],
                  answer="TFF", display="True, false, false", hint="What did the letters range over?",
                  explanation="Equality needs both coordinates, and only the fixed factor $2-i$ was covered."),
        ],
        tier=2, display="$(\\alpha+\\beta)(2-i)=\\alpha(2-i)+\\beta(2-i)$",
        solution="Both routes give $(2a+b+2c+d)+(2b-a+2d-c)i$.", targets="transfer on another statement (recovery)",
    )
    slot_error = _question(
        key="k1q3", tier=2, kind="expression",
        prompt="To prove $(1-i)(\\alpha+\\beta)=(1-i)\\alpha+(1-i)\\beta$ for $\\alpha=a+bi$, $\\beta=c+di$, a learner "
        "computed $(1-i)\\alpha$ with the slots $x=1$, $y=1$, $u=a$, $v=b$. Enter the correct $(1-i)\\alpha$ as a complex "
        "number in standard form (e.g. $3-2i$)." + _marker(None, "a+b+(b-a)*i"),
        answer="a+b+(b-a)*i", answer_display="$(a+b)+(b-a)i$",
        hints=["Which number is $y$ for the factor $1-i$?", "$y=-1$.", "Real part $a-(-1)b$, imaginary part $b+(-1)a$."],
        solution="With slots $1,-1,a,b$: real part $a+b$, imaginary part $b-a$.", targets="spot the slot error",
    )
    return KnowledgePointDraft(
        title="Transfer: prove a distributive law with two routes",
        explanation="1. Say what each route does. 2. Compute each route on its own from Definition 1.1, filling the slots "
        "before every product. 3. Compare both coordinates, naming the real law. 4. The letters were arbitrary, so the "
        "law holds for all inputs.",
        worked_example=WorkedExample(
            problem="Prove $\\alpha\\beta=\\beta\\alpha$ for all $\\alpha=a+bi$, $\\beta=c+di$ (Example 1.4).",
            steps=[
                WorkedStep(math="$\\alpha\\beta$: slots $a,b,c,d$ give $(ac-bd)+(ad+bc)i$.", why="Definition 1.1."),
                WorkedStep(math="$\\beta\\alpha$: slots $c,d,a,b$ give $(ca-db)+(cb+da)i$.", why="Reset the slots: $\\beta$ comes first."),
                WorkedStep(math="$ac-bd=ca-db$ and $ad+bc=cb+da$.", why="Real products and sums can be reordered."),
            ],
            takeaway="Compute each side from the definition, then compare every coordinate.",
        ),
        target_questions=1,
        questions=[walkthrough, recovery, slot_error],
    )


def _scope_point() -> KnowledgePointDraft:
    served = _question(
        key="k2q1", tier=2, kind="true_false_set",
        prompt="Mark each claim about an argument true or false." + _marker(None, "FTFFT"),
        choices=[
            "Computing $(2+i)(1-3i)$ and $(1-3i)(2+i)$ and getting the same number proves $\\alpha\\beta=\\beta\\alpha$ for all complex $\\alpha,\\beta$.",
            "Computing $\\alpha\\beta$ and $\\beta\\alpha$ from Definition 1.1 with $\\alpha=a+bi$, $\\beta=c+di$ for arbitrary real $a,b,c,d$, and finding equal coordinates, proves $\\alpha\\beta=\\beta\\alpha$.",
            "Proving $(\\alpha\\beta)i=\\alpha(\\beta i)$ for all $\\alpha,\\beta$ also proves $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ for every complex $\\lambda$.",
            "A proof of $\\lambda(\\alpha+\\beta)=\\lambda\\alpha+\\lambda\\beta$ may expand the brackets term by term and replace $i^2$ by $-1$.",
            "In that proof, with $\\lambda=g+hi$, the real number $g(a+c)$ may be rewritten as $ga+gc$.",
        ],
        answer="FTFFT", answer_display="False, true, false, false, true",
        hints=["Which claims cover every input?", "A fixed input or a numeric check covers less.", "Expanding complex brackets uses the law itself."],
        solution="1 checks one case; 2 covers all inputs; 3 generalizes a fixed input; 4 is circular; 5 is a real law.",
        targets="what a proof establishes",
    )
    second = _question(
        key="k2q2", tier=2, kind="true_false_set",
        prompt="Mark each claim true or false." + _marker(None, "FTFT"),
        choices=[
            "Agreement of the real coordinates alone proves two complex numbers equal.",
            "Writing $1=1+0i$ and computing $(g+hi)(1+0i)$ from Definition 1.1 for arbitrary real $g,h$ proves $\\lambda\\cdot1=\\lambda$.",
            "A proof of $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ may start from that equation and simplify both sides.",
            "In a proof of $\\alpha\\beta=\\beta\\alpha$, $ac=ca$ may be used for real $a,c$.",
        ],
        answer="FTFT", answer_display="False, true, false, true",
        hints=["Equality needs every coordinate.", "Real laws are allowed.", "Starting from the claim assumes it."],
        solution="1 compares one coordinate; 2 covers all $\\lambda$; 3 assumes the claim; 4 is a real law.",
        targets="what a proof establishes (recovery)",
    )
    decide = _multistep(
        "k2q3",
        "Does $(1+i)(\\alpha\\beta)=((1+i)\\alpha)((1+i)\\beta)$ hold for all $\\alpha=a+bi$, $\\beta=c+di$? Compute both sides.",
        [
            _step("expression", "Enter $(1+i)(\\alpha\\beta)$ in standard form (e.g. $3-2i$); the coordinates may stay unexpanded.",
                  answer="a*c-b*d-a*d-b*c+(a*c-b*d+a*d+b*c)*i", display="$(ac-bd-ad-bc)+(ac-bd+ad+bc)i$",
                  hint="$\\alpha\\beta=(ac-bd)+(ad+bc)i$; the slots are $1,1,ac-bd,ad+bc$.",
                  explanation="Real $(ac-bd)-(ad+bc)$, imaginary $(ad+bc)+(ac-bd)$."),
            _step("expression", "Given $(1+i)\\alpha=(a-b)+(a+b)i$ and $(1+i)\\beta=(c-d)+(c+d)i$, enter their product in "
                  "standard form; the coordinates may stay unexpanded.",
                  answer="-2*a*d-2*b*c+(2*a*c-2*b*d)*i", display="$(-2ad-2bc)+(2ac-2bd)i$",
                  hint="The slots are $a-b,a+b,c-d,c+d$.", explanation="Real $(a-b)(c-d)-(a+b)(c+d)$, imaginary $(a-b)(c+d)+(a+b)(c-d)$."),
            _step("true_false", "The two sides agree for every $\\alpha,\\beta$.", choices=["True", "False"], correct=1,
                  display="False", hint="Try $\\alpha=\\beta=1$.",
                  explanation="With $\\alpha=\\beta=1$ the left side is $1+i$ and the right side is $(1+i)^2=2i$."),
        ],
        tier=3, display="No", solution="The coordinates differ, e.g. at $\\alpha=\\beta=1$.", targets="decide a proposed identity",
    )
    return KnowledgePointDraft(
        title="What a proof establishes",
        explanation="A proof covers exactly the inputs its letters range over: a fixed input proves only that case, and a "
        "numeric check proves only itself. Definitions and real-number laws may be used; the law being proved may not.",
        worked_example=WorkedExample(
            problem="Prove $(\\alpha+\\beta)+\\lambda=\\alpha+(\\beta+\\lambda)$ and $\\lambda\\cdot 1=\\lambda$ for all "
            "$\\alpha=a+bi$, $\\beta=c+di$, $\\lambda=g+hi$.",
            steps=[
                WorkedStep(math="$((a+c)+g)+((b+d)+h)i$ and $(a+(c+g))+(b+(d+h))i$",
                           why="Definition 1.1 on each route; the coordinates agree by real associativity."),
                WorkedStep(math="$\\lambda\\cdot 1$: slots $g,h,1,0$ give $(g\\cdot1-h\\cdot0)+(g\\cdot0+h\\cdot1)i$",
                           why="Write $1=1+0i$ and apply Definition 1.1."),
                WorkedStep(math="$a,b,c,d,g,h$ were arbitrary", why="Checking $\\alpha=1$, $\\beta=i$, $\\lambda=2$ would show the laws only for those numbers."),
            ],
            takeaway="A proof covers exactly the inputs its letters range over.",
        ),
        target_questions=1,
        questions=[served, second, decide],
    )


def _proof_capstone() -> QuestionDraft:
    return _multistep(
        "cap",
        "Strategy: two routes, same destination. Let $\\mu=(2-i)(1+i)$. Prove $(\\alpha\\beta)\\mu=(\\alpha\\mu)\\beta$ for all "
        "$\\alpha=a+bi$, $\\beta=c+di$ ($a,b,c,d$ real) from Definition 1.1 and real-number laws, then use it.",
        [
            _step("expression", "Enter $\\mu$ as a complex number in standard form (e.g. $3-2i$).", answer="3+i",
                  display="$3+i$", hint="Multiply $(2-i)(1+i)$ as in S1.", explanation="$2+2i-i+1=3+i$."),
            _step("expression", "Route A multiplies $\\alpha$ and $\\beta$, then multiplies by $\\mu$. Enter "
                  "$(\\alpha\\beta)\\mu$ in standard form; the coordinates may stay unexpanded.",
                  answer="3*a*c-3*b*d-a*d-b*c+(a*c-b*d+3*a*d+3*b*c)*i", display="$(3ac-3bd-ad-bc)+(ac-bd+3ad+3bc)i$",
                  hint="$\\alpha\\beta=(ac-bd)+(ad+bc)i$: the slots are $ac-bd$, $ad+bc$, $3$, $1$.",
                  explanation="Real $3(ac-bd)-(ad+bc)$, imaginary $(ac-bd)+3(ad+bc)$."),
            _step("expression", "Route B multiplies $\\alpha$ by $\\mu$ first, then multiplies the result by $\\beta$. "
                  "Compute $\\alpha\\mu$, then enter the four slots $(x,y,u,v)$ of $(\\alpha\\mu)\\beta$, in this order.",
                  answer="(3*a-b, a+3*b, c, d)", display="$(3a-b,\\ a+3b,\\ c,\\ d)$",
                  hint="$\\alpha\\mu=(a+bi)(3+i)$.", explanation="$\\alpha\\mu=(3a-b)+(a+3b)i$; route B then meets route A."),
            _step("expression", "Suppose $\\alpha\\beta=2-5i$ for some $\\alpha,\\beta$ you are not told. Use the law to enter "
                  "$(\\alpha\\mu)\\beta$ in standard form.", answer="11-13*i", display="$11-13i$",
                  hint="$(\\alpha\\mu)\\beta=(\\alpha\\beta)\\mu$.", explanation="$(2-5i)(3+i)=6+2i-15i+5=11-13i$."),
        ],
        display="$11-13i$",
        solution="$\\mu=3+i$; both routes give $(3ac-3bd-ad-bc)+(ac-bd+3ad+3bc)i$, so $(\\alpha\\mu)\\beta=(\\alpha\\beta)\\mu=(2-5i)(3+i)=11-13i$.",
        targets="prove a new statement of the family and use it",
    )


def proof_lesson(title: str, objective: str, key: str) -> LessonV2Draft:
    return LessonV2Draft(
        title=title,
        objective=objective,
        goal_line="Prove a law such as $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$ by computing both sides from "
        "Definition 1.1 and comparing their coordinates.",
        blocks=_blocks(),
        notation=[
            NotationItem(symbol="$\\alpha=a+bi,\\ \\beta=c+di,\\ \\lambda=g+hi$", meaning="the inputs in coordinates ($a,\\dots,h$ real)"),
            NotationItem(symbol="$x,y,u,v$", meaning="the four slots of $(x+yi)(u+vi)$"),
        ],
        knowledge_points=[_transfer_point(), _scope_point()],
        capstone=_proof_capstone(),
        connection=f"{key}: the same two routes prove the laws of $\\mathbf{{F}}^n$ entry by entry.",
        deeper="Multiplying by $i$ sends the coordinates $(x,y)$ to $(-y,x)$.",
        source_refs=["1.3", "1.4"],
    )
