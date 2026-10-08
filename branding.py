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
        "name": "NY State Homeowner & Tenant Helpline",
        "detail": "Statewide help, including rent-regulated matters handled by DHCR/HCR.",
        "contact": "(855) 466-3456",
        "url": "https://hcr.ny.gov/",
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

    @app.context_processor
    def _inject_branding():
        return {
            "product_name": PRODUCT_NAME,
            "logo_monogram": LOGO_MONOGRAM,
            "short_disclaimer": SHORT_DISCLAIMER,
            "top_disclaimer": TOP_DISCLAIMER,
            "top_disclaimer_detail": TOP_DISCLAIMER_DETAIL,
            "privacy_note_title": PRIVACY_NOTE_TITLE,
            "privacy_note": PRIVACY_NOTE,
            "privacy_note_detail": PRIVACY_NOTE_DETAIL,
            "law_page_note_detail": LAW_PAGE_NOTE_DETAIL,
            "per_message_disclaimer": PER_MESSAGE_DISCLAIMER,
            "learn_more_label": LEARN_MORE_LABEL,
            "law_page_note": LAW_PAGE_NOTE,
        }
