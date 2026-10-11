"""Product name and the legal notices shown to users.

WHY THIS IS A MODULE AND NOT JUST TEXT IN THE TEMPLATES
There is no shared layout in this app -- every template is standalone and
repeats its own styling (see docs/frontend/README.md). That is a deliberate
choice for CSS, but it is a bad one for legal wording: six hand-copied
disclaimers drift, and the one that drifts is the one that ends up quoted
back at you. So the words live here, once, and app.py injects them into
every template through a context processor. Each page styles them however
it likes; none of them owns the text.

Changing SHORT_DISCLAIMER or the Learn More copy changes it everywhere at
once, including pages added later, which is the point.
"""

PRODUCT_NAME = "SideKick Tidbit"

# The monogram shown in the brand mark until a real logo exists.
LOGO_MONOGRAM = "ST"

# The persistent fine print. Kept to one sentence because it sits under the
# message composer on every turn -- long enough to actually say "not legal
# advice", short enough that people still read it after the tenth time.
SHORT_DISCLAIMER = (
    f"{PRODUCT_NAME} gives general information, not legal advice."
)

# Under the text of a law on /law/<citation>. The page shows the statute
# itself, which invites reading it as an answer; this says what the text
# can and cannot tell someone, next to the text rather than only in the
# footer.
LAW_PAGE_NOTE = (
    "Official text. How it applies depends on your facts; this is not legal advice."
)
LAW_PAGE_NOTE_DETAIL = (
    "Other laws and court decisions can change how a section applies to you, "
    "so treat the text as a starting point."
)

# The lock note beside the name and date-of-birth fields (sign-up,
# Settings, the requirements page).
#
# Public wording describes the purpose and available controls, not the
# implementation (owner, 2026-10-06). Keep providers, algorithms and
# configuration in internal documentation. Do not make blanket "never
# shared" claims about a site whose chat uses an external processor.
PRIVACY_NOTE_TITLE = "Your details stay confidential."
PRIVACY_NOTE = "Used for your account."
PRIVACY_NOTE_DETAIL = "You can update these details in Settings."

# Shown next to the short disclaimer as the link out to the full page.
LEARN_MORE_LABEL = "Learn more"

# The privacy page (/privacy), linked from every footer and from sign-up.
#
# It describes the usage counts (usage_metrics.py) and points to where
# account data is managed. Every sentence here is a promise the code has
# to keep: tests/test_usage_counts.py checks the ones that can be checked
# (what the recorder accepts, what the table can hold, no new cookies).
# NOT REVIEWED BY A LAWYER as of 2026-10-11; see the log entry of that
# date. Scope each "never" to the usage counts: the site as a whole does
# handle account details and sends chat messages to be answered.
PRIVACY_LABEL = "Privacy"
PRIVACY_TITLE = "Privacy"
PRIVACY_LEDE = "What we count to improve the site, and what we never keep in those counts."
PRIVACY_SECTIONS = [
    {
        "title": "What we count",
        "intro": "We keep simple daily totals so we can see what is useful and where people get stuck.",
        "points": [
            "How many times each kind of page was opened.",
            "How many street and building searches found a result, and how many did not.",
            "Which borough was searched, and whether a ZIP code or cross street was added (yes or no, never the value).",
            "Which sections of the law were opened.",
            "How many chat messages were sent, and how many replies cited the law.",
            "Which help links and suggestion buttons were used.",
            "How many times each step of sign-up or a form was reached.",
            "How many errors happened, and on which kind of page.",
            "Whether visits were on a phone or a computer, the browser's main language, and whether "
            "a visit came from a search engine, social media or directly, and whether it was signed in.",
        ],
    },
    {
        "title": "What these counts never include",
        "intro": "A count is a day, a category and a number. It does not say who did what.",
        "points": [
            "No name, email address or account.",
            "No street, address, apartment number, ZIP code or cross street.",
            "Nothing you typed, including your chat messages and their topics.",
            "No IP address, and no cookie or code that follows you from visit to visit.",
            "No web addresses, which can contain what you searched for.",
            "Nothing from other websites. We use no advertising or tracking services.",
        ],
    },
    {
        "title": "How long we keep them",
        "intro": "Daily totals are deleted after 12 months. When we look at them, any total under 5 is hidden.",
        "points": [],
    },
    {
        "title": "Your choice",
        "intro": "If your browser sends the Global Privacy Control signal, your visits are not counted at all.",
        "points": [],
    },
    {
        "title": "Your language",
        "intro": "If you pick a language, that choice is remembered on this device, and on your account if you are signed in. It is not used for the counts.",
        "points": [],
    },
]
PRIVACY_ACCOUNT_TITLE = "Your account and chats"
PRIVACY_ACCOUNT_TEXT = (
    "This page is about usage counts. Your account details and saved chats are separate: you can "
    "download your chats or delete your account in Settings."
)

# The banner across the top of the app. Longer and louder than
# SHORT_DISCLAIMER on purpose: the footer note is the standing reminder,
# this is the thing someone in real trouble should hit before they start
# typing. First written by the product owner; trimmed at the owner's request
# on 2026-10-02 (every page was too wordy), with the rest moved behind an
# "i" button (TOP_DISCLAIMER_DETAIL).
#
# NOTE: this text makes a promise -- that asking the chatbot for legal help
# produces a list of contacts. ai_service.py's system prompt is what keeps
# that promise (see the referral rule there), and HELP_RESOURCES below is
# the list it draws on. Change one and check the other two.
TOP_DISCLAIMER = (
    "Not legal advice. Need a lawyer? Tell the chat and it will list free "
    "legal help near you."
)

# Behind the banner's "i" button. Shortened 2026-10-02 at the owner's
# request (pages were too wordy); the full owner-written wording's points
# all survive between the banner and this.
TOP_DISCLAIMER_DETAIL = (
    f"{PRODUCT_NAME} gives general information about NYC tenant law. It is not "
    "a law firm and should never be used as legal advice. For your own "
    "situation, talk to a housing attorney."
)

# Sits under every single assistant reply. This one has to stay very short:
# it repeats on every turn, and a paragraph repeated twenty times is a
# paragraph nobody reads. Its job is to make sure no individual message can
# be screenshotted or quoted without the qualifier attached to it.
PER_MESSAGE_DISCLAIMER = "General information, not legal advice."

# The full version, on /learn-more: one short line each, with the rest
# behind an "i" button (owner, 2026-10-02: pages were too wordy). Together
# the lead and detail still say everything the earlier paragraphs did.
FULL_DISCLAIMER_POINTS = [
    {
        "lead": "Not a law firm. Using it does not make us your lawyer.",
        "detail": (
            f"{PRODUCT_NAME} explains how NYC housing rules generally work and helps "
            "you prepare a complaint form. It does not provide legal advice, and "
            "using it does not create an attorney-client relationship."
        ),
    },
    {
        "lead": "Your facts matter. One changed date can change the answer.",
        "detail": (
            "Nothing here replaces advice from a licensed attorney about your "
            "specific situation."
        ),
    },
    {
        "lead": "The AI can be wrong. Confirm before you act.",
        "detail": (
            "It can misstate a rule, miss an exception, or be out of date. Check "
            "deadlines, dollar amounts and anything about a court case with an "
            "official source or a housing attorney."
        ),
    },
    {
        "lead": "Forms it fills are drafts. Read every field before you sign or file.",
        "detail": "You are responsible for what you submit to an agency or a court.",
    },
]

# Situations where the assistant should say out loud, in its own reply, that
# this is not legal advice -- not just leave it to the footer. Mirrored in
# ai_service.py's system prompt; kept here so the product decision about
# WHEN to speak up lives beside the words themselves.
ESCALATION_TRIGGERS = [
    "an active or threatened court case, including an eviction proceeding",
    "a deadline that has passed or is about to",
    "a document they have already signed, or are being asked to sign",
    "anything involving money they could lose or owe",
    "a decision that is hard to reverse once made",
]

# Where to send someone who needs a real lawyer. Free/low-cost first,
# because that is the realistic route for most tenants this app serves.
HELP_RESOURCES = [
    {
        "name": "NYC Tenant Helpline",
        "detail": "Free housing advice and referrals, run by the City.",
        "contact": "311, or ask for the Tenant Helpline",
        "url": "https://www.nyc.gov/site/hpd/services-and-information/tenants.page",
    },
    {
        "name": "Housing Court Answers",
        "detail": "Help understanding a Housing Court case, notice, or paper you were served.",
        "contact": "(212) 962-4795",
        "url": "https://housingcourtanswers.org/",
    },
    {
        "name": "NYC Right to Counsel",
        "detail": "Free legal representation in eviction cases for tenants who qualify.",
        "contact": "311",
        "url": "https://www.nyc.gov/site/hra/help/legal-services-for-tenants-facing-eviction.page",
    },
    {
        # Was "NY State Homeowner & Tenant Helpline, (855) 466-3456" until
        # 2026-10-07 -- that number is the State's Homeowner Protection
        # Program, not a tenant line. Replaced with the State rent office.
        "name": "NY State rent office (HCR)",
        "detail": "Questions about rent-stabilized apartments, rent history and overcharges.",
        "contact": "1 (833) 499-0343",
        "url": "https://hcr.ny.gov/contact-us",
    },
    {
        "name": "Met Council on Housing hotline",
        "detail": "Free tenant-rights advice by phone, Monday, Wednesday and Friday afternoons.",
        "contact": "(212) 979-0611",
        "url": "https://www.metcouncilonhousing.org/program/tenants-rights-hotline/",
    },
    {
        "name": "HPD complaints",
        "detail": "File a repair, heat or hot water complaint against a landlord.",
        "contact": "311, or file online",
        "url": "https://portal.311.nyc.gov/",
    },
]


def register(app) -> None:
    """Make the product name and fine print available to every template.

    Called by create_app() and by the test-app helper, so a bare Flask app
    built in a test renders the same strings production does. Without a
    shared function here, the test apps quietly rendered an empty product
    name and no disclaimer at all -- the page still came out, just with the
    branding missing, which is the kind of thing a test suite happily
    stays green about.
    """

    # Translation is set up here because this is the one function both
    # create_app() and the test-app helper already call (see i18n.py).
    import i18n

    i18n.init_app(app)

    @app.context_processor
    def _inject_branding():
        # The constants above stay plain English strings (everything that
        # compares or reuses them depends on that). They are translated
        # here, where they are used, and because this is the wording a
        # tenant relies on, each comes with its English original on a
        # translated page (i18n.with_original). On an English page these
        # are the same strings as ever. The product name is a name.
        legal = i18n.with_original
        return {
            "product_name": PRODUCT_NAME,
            "logo_monogram": LOGO_MONOGRAM,
            "short_disclaimer": legal(SHORT_DISCLAIMER),
            "top_disclaimer": legal(TOP_DISCLAIMER),
            "top_disclaimer_detail": legal(TOP_DISCLAIMER_DETAIL),
            "privacy_note_title": legal(PRIVACY_NOTE_TITLE),
            "privacy_note": legal(PRIVACY_NOTE),
            "privacy_note_detail": legal(PRIVACY_NOTE_DETAIL),
            "law_page_note_detail": legal(LAW_PAGE_NOTE_DETAIL),
            "per_message_disclaimer": legal(PER_MESSAGE_DISCLAIMER),
            # For the chat script, which builds each note itself.
            "per_message_disclaimer_text": i18n.gettext(PER_MESSAGE_DISCLAIMER),
            "per_message_disclaimer_english": i18n.english_if_translated(PER_MESSAGE_DISCLAIMER),
            "learn_more_label": i18n.gettext(LEARN_MORE_LABEL),
            "privacy_label": i18n.gettext(PRIVACY_LABEL),
            "law_page_note": legal(LAW_PAGE_NOTE),
        }
