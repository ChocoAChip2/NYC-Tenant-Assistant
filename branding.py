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

# Shown next to the short disclaimer as the link out to the full page.
LEARN_MORE_LABEL = "Learn more"

# The full version, on /learn-more. This is the one that has to be
# complete rather than brief.
FULL_DISCLAIMER_PARAGRAPHS = [
    (
        f"{PRODUCT_NAME} is an informational tool for New York City tenants. It "
        "explains how housing rules generally work and helps you prepare a "
        "complaint form. It is not a law firm, it does not provide legal "
        "advice, and using it does not create an attorney-client relationship."
    ),
    (
        "Nothing here is a substitute for advice from a licensed attorney about "
        "your specific situation. Housing law is fact-specific: the same facts "
        "with one date changed can lead to a completely different answer."
    ),
    (
        "The assistant is an AI system and can be wrong. It can misstate a rule, "
        "miss an exception, or be out of date. Confirm anything you intend to "
        "act on with an official source or a housing attorney before you rely "
        "on it -- especially deadlines, dollar amounts, and anything involving "
        "a court case."
    ),
    (
        f"Documents {PRODUCT_NAME} fills in for you are drafts. Read every "
        "field before you sign or file anything. You remain responsible for "
        "what you submit to a government agency or a court."
    ),
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
            "learn_more_label": LEARN_MORE_LABEL,
        }
