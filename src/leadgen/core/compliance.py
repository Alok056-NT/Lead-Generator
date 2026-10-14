"""Cold-email compliance table: one researched rule per country in the country table.

Each `EmailRule` gives the legal regime, the consent model, the elements every message must
carry, a default policy for the sending gate (T14), a plain-English summary and the sources
that were read. The sources and dates are also listed in docs/DECISIONS.md. This is guidance
for a tool, not legal advice; laws change, so re-check `checked_on` dates older than 6 months.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from leadgen.core import countries

CONSENT_MODELS = ("opt_out", "opt_in", "opt_in_with_exceptions")
DEFAULT_POLICIES = ("allow_with_optout", "warn", "block_without_consent")
REQUIRED_ELEMENTS = (
    "sender_identity",
    "postal_address",
    "unsubscribe",
    "truthful_subject",
    "subject_label",
)
CHECKED_ON = "2026-10-14"
DISCLAIMER = "This is not legal advice."


@dataclass(frozen=True)
class EmailRule:
    """Cold-email rule for one country (see module docstring for the meaning of each field)."""

    country_iso2: str
    regime: str
    consent_model: str
    required_elements: tuple[str, ...]
    subject_label: str | None
    default_policy: str
    summary: str
    sources: tuple[str, ...]
    checked_on: str

    def __post_init__(self) -> None:
        if self.consent_model not in CONSENT_MODELS:
            raise ValueError(f"{self.country_iso2}: bad consent_model {self.consent_model!r}")
        if self.default_policy not in DEFAULT_POLICIES:
            raise ValueError(f"{self.country_iso2}: bad default_policy {self.default_policy!r}")
        bad = [e for e in self.required_elements if e not in REQUIRED_ELEMENTS]
        if bad:
            raise ValueError(f"{self.country_iso2}: bad required_elements {bad}")
        if ("subject_label" in self.required_elements) != bool(self.subject_label):
            raise ValueError(f"{self.country_iso2}: subject_label and required_elements disagree")


_BASE_ELEMENTS = ("sender_identity", "unsubscribe", "truthful_subject")
_WITH_ADDRESS = ("sender_identity", "postal_address", "unsubscribe", "truthful_subject")


def _dla(iso2: str) -> str:
    """DLA Piper "Data protection laws of the world" e-marketing page (law-firm summary)."""
    return f"https://www.dlapiperdataprotection.com/index.html?t=electronic-marketing&c={iso2}"


_EPRIVACY = (
    "https://eur-lex.europa.eu/eli/dir/2002/58/oj",
    "https://prighter.com/resources/laws/eprivacy-directive/articles/article-13",
)

# Shared base for EU/EEA members: ePrivacy Directive Article 13 plus GDPR Article 21.
_EU_BASE = EmailRule(
    country_iso2="EU",
    regime="ePrivacy Directive 2002/58/EC Art. 13 (national law) and GDPR Art. 6 and 21",
    consent_model="opt_in_with_exceptions",
    required_elements=_BASE_ELEMENTS,
    subject_label=None,
    default_policy="warn",
    summary=(
        "EU law requires prior consent for marketing email to individuals, with a narrow "
        "exception for existing customers who were offered an opt-out. How business email "
        "is treated is left to each country, and an address that names a person is also "
        "personal data under GDPR. Identify yourself, include a free unsubscribe and stop "
        "when someone objects. " + DISCLAIMER
    ),
    sources=_EPRIVACY,
    checked_on=CHECKED_ON,
)


def _eu(iso2: str, regime: str, summary: str, *extra_sources: str, **overrides) -> EmailRule:
    """Build a national rule from the shared EU base."""
    return replace(
        _EU_BASE,
        country_iso2=iso2,
        regime=regime,
        summary=summary + " " + DISCLAIMER,
        sources=(*extra_sources, *_EPRIVACY),
        **overrides,
    )


def _rule(
    iso2: str,
    regime: str,
    consent_model: str,
    default_policy: str,
    summary: str,
    sources: tuple[str, ...],
    required: tuple[str, ...] = _BASE_ELEMENTS,
    label: str | None = None,
) -> EmailRule:
    """Build a rule that is not derived from the EU base."""
    return EmailRule(
        country_iso2=iso2,
        regime=regime,
        consent_model=consent_model,
        required_elements=required,
        subject_label=label,
        default_policy=default_policy,
        summary=summary + " " + DISCLAIMER,
        sources=sources,
        checked_on=CHECKED_ON,
    )


_STRICT = "block_without_consent"

_RULE_LIST: list[EmailRule] = [
    # --- Asia-Pacific ---
    _rule(
        "SG",
        "Spam Control Act 2007 (bulk unsolicited commercial email); "
        "PDPA Do Not Call rules cover phone numbers only",
        "opt_out",
        "allow_with_optout",
        "Singapore does not require consent for unsolicited commercial email, but once you send "
        "in bulk (more than 100 similar messages in 24 hours, 1,000 in 30 days or 10,000 in a "
        "year) the subject must start with <ADV>, be truthful, and the message must carry an "
        "English unsubscribe statement with a working address that is honoured within 10 business "
        "days. This tool adds <ADV> and the unsubscribe line to every Singapore email to be safe.",
        ("https://sso.agc.gov.sg/Act/SCA2007", _dla("SG")),
        _BASE_ELEMENTS + ("subject_label",),
        "<ADV>",
    ),
    _rule(
        "MY",
        "Personal Data Protection Act 2010 (Act 709); anti-spam rules under the Communications "
        "and Multimedia Act proposed by MCMC in 2025, not yet in force",
        "opt_out",
        "warn",
        "Malaysia has no anti-spam rule in force for email yet; the Personal Data Protection Act "
        "lets people require you to stop using their data for direct marketing. The regulator "
        "has consulted on a rule needing consent and a free unsubscribe, so expect tightening. "
        "Identify yourself, add an unsubscribe line and stop immediately when asked.",
        (_dla("MY"),),
    ),
    _rule(
        "ID",
        "Personal Data Protection Law (Law 27 of 2022)",
        "opt_in_with_exceptions",
        "warn",
        "Indonesia has no dedicated email marketing law, but the data protection law needs a "
        "lawful basis (usually consent) to use someone's personal data and lets people withdraw "
        "consent. Treat business contact data carefully, identify yourself and honour "
        "unsubscribe requests straight away.",
        (_dla("ID"),),
    ),
    _rule(
        "PH",
        "Data Privacy Act 2012; DTI-DOH-DA Joint Administrative Order 01 of 2008 (e-commerce)",
        "opt_out",
        "warn",
        "The Philippines has no dedicated anti-spam law for business email, but the Data Privacy "
        "Act gives people the right to object to direct marketing and requires clear information "
        "about how their data is used. Identify yourself, keep messages truthful and stop when "
        "someone objects.",
        (_dla("PH"),),
    ),
    _rule(
        "TH",
        "Personal Data Protection Act 2019",
        "opt_out",
        "warn",
        "Thailand's data protection law gives people the right to object to direct marketing, so "
        "an opt-out must work for as long as you contact them. There is no separate consent "
        "rule for business email in the sources checked. Identify yourself and stop when asked.",
        (_dla("TH"),),
    ),
    _rule(
        "VN",
        "Decree 91/2020/ND-CP on anti-spam messages, emails and calls",
        "opt_in_with_exceptions",
        _STRICT,
        "Vietnam allows advertising email only within tight rules: the advertiser must be "
        "identifiable, every email needs a working opt-out, the subject must start with [QC] "
        "(or [AD]), and you may not keep sending after a refusal (no more than three ads per "
        "day). Do not send cold advertising email to Vietnamese recipients without their "
        "agreement.",
        (
            _dla("VN"),
            "https://lawnet.vn/thong-tin-phap-luat/en/chinh-sach-moi/vietnam-05-advertising-email-requirements-115216.html",
        ),
        _BASE_ELEMENTS + ("subject_label",),
        "[QC]",
    ),
    _rule(
        "IN",
        "Information Technology Act 2000; Digital Personal Data Protection Act 2023 and Rules",
        "opt_out",
        "warn",
        "India has no anti-spam statute for email; telecom marketing rules cover calls and "
        "SMS, and the new data protection law lets people withdraw consent for marketing. "
        "Cold business email is not banned, but be accurate about who you are, include a "
        "working unsubscribe and stop when asked.",
        (_dla("IN"),),
    ),
    _rule(
        "AE",
        "TDRA Unsolicited Electronic Communications Regulation; Federal Decree-Law 45 of 2021 "
        "(PDPL); Federal Decree-Law 14 of 2023 (trading by modern technological means)",
        "opt_in_with_exceptions",
        "warn",
        "The UAE has no single email marketing law. The telecoms regulator treats marketing "
        "sent without the recipient's consent as spam, the data protection law lets people "
        "stop direct marketing, and consumers can choose not to receive marketing email. "
        "Treat consent as the safer default, identify yourself and honour opt-outs.",
        (_dla("AE"),),
    ),
    _rule(
        "SA",
        "Personal Data Protection Law (PDPL) and Implementing Regulations",
        "opt_in_with_exceptions",
        "warn",
        "Saudi Arabia's data protection law sets conditions for using personal data for direct "
        "marketing, and e-commerce rules may add more. The sources checked did not give a "
        "simple business-email exception, so treat consent as the safer default, identify "
        "yourself and honour opt-outs.",
        (_dla("SA"),),
    ),
    _rule(
        "QA",
        "Personal Data Privacy Protection Law (Law 13 of 2016)",
        "opt_in",
        _STRICT,
        "Qatar requires prior, explicit consent before sending electronic marketing. Every "
        "message must show who sent it, say that it is direct marketing, give a reachable "
        "address and let the recipient withdraw consent at any time. Do not send cold email "
        "to Qatar without consent.",
        (_dla("QA"),),
        _BASE_ELEMENTS,
    ),
    _rule(
        "AU",
        "Spam Act 2003 (Cth), enforced by ACMA",
        "opt_in_with_exceptions",
        _STRICT,
        "Australia requires consent before sending commercial email, but consent can be inferred "
        "from a work address that is conspicuously published (and not marked as no-spam) when "
        "your message relates to the person's role. Every message needs accurate sender "
        "identity and a working unsubscribe that is processed within 5 working days. Addresses "
        "guessed from a name pattern do not qualify, so record your basis for each recipient.",
        (
            "https://www.legislation.gov.au/C2004A01214/2016-03-10/2016-03-10/text/original/pdf",
            _dla("AU"),
        ),
    ),
    _rule(
        "NZ",
        "Unsolicited Electronic Messages Act 2007",
        "opt_in_with_exceptions",
        _STRICT,
        "New Zealand requires express, inferred or deemed consent before commercial email. "
        "Deemed consent applies when someone conspicuously publishes a work address (without "
        "a no-unsolicited-messages notice) and your message relates to their role. Every "
        "message must identify the sender and have a free unsubscribe honoured within five "
        "working days; addresses guessed from a pattern do not qualify.",
        ("https://www.dia.govt.nz/Spam-Three-Steps", _dla("NZ")),
    ),
    _rule(
        "JP",
        "Act on Regulation of Transmission of Specified Electronic Mail (Act 26 of 2002); "
        "Act on Specified Commercial Transactions",
        "opt_in_with_exceptions",
        _STRICT,
        "Japan requires prior consent for advertising email, with exceptions for people who gave "
        "you their address (for example a business card), people you already do business with, "
        "and businesses that publicly disclosed their address. Each email must show the sender's "
        "name and address and an opt-out address, and you must stop once someone refuses. "
        "Addresses guessed from a pattern do not qualify.",
        (
            "https://www.soumu.go.jp/main_sosiki/joho_tsusin/eng/Resources/laws/Specified-E-mail-index.pdf",
            "https://monolith.law/en/it/e-mail-newsletter-opt-in-procedure",
            _dla("JP"),
        ),
        _WITH_ADDRESS,
    ),
    _rule(
        "KR",
        "Act on Promotion of Information and Communications Network Utilization and Information "
        "Protection (Network Act) Art. 50",
        "opt_in_with_exceptions",
        _STRICT,
        "South Korea requires the recipient's explicit prior consent before commercial "
        "electronic advertising is sent, with a limited exception for your own recent customers. "
        "The message must show who you are and how to refuse, and you must stop after a "
        "refusal. Korean rules may also require a marking in the subject line that was not "
        "verified here, so check before sending.",
        (_dla("KR"),),
    ),
    _rule(
        "HK",
        "Unsolicited Electronic Messages Ordinance (Cap. 593); Personal Data (Privacy) Ordinance",
        "opt_out",
        "warn",
        "Hong Kong lets you send commercial email without prior consent, but each message must "
        "carry accurate sender information and a working unsubscribe, subjects must not "
        "mislead, and you must stop once someone opts out. Using a named person's data for "
        "direct marketing also needs the notice and opt-out steps of the privacy ordinance.",
        ("https://www.elegislation.gov.hk/hk/cap593", _dla("HK")),
    ),
    _rule(
        "TW",
        "Personal Data Protection Act",
        "opt_out",
        "warn",
        "Taiwan has no dedicated anti-spam law in the sources checked. When you first market to "
        "someone using their personal data you must tell them they can ask you to stop and how, "
        "and you must stop when they do. Identify yourself and keep messages truthful.",
        (_dla("TW"),),
    ),
    _rule(
        "CN",
        "Measures for the Administration of Internet E-mail Services (2006); Personal "
        "Information Protection Law",
        "opt_in",
        _STRICT,
        "China requires the recipient's explicit consent for marketing email. Each message must "
        "identify the sender and carry the mark 广告 or AD in the subject. Do not send cold "
        "email to recipients in China without consent.",
        (_dla("CN"),),
        _BASE_ELEMENTS + ("subject_label",),
        "AD",
    ),
    # --- Europe (UK, Ireland, EU/EEA, Switzerland) ---
    _rule(
        "GB",
        "Privacy and Electronic Communications Regulations 2003 (PECR); UK GDPR",
        "opt_out",
        "warn",
        "Companies and other corporate bodies can be emailed without prior consent, but sole "
        "traders and some partnerships count as individuals and need consent (or a soft "
        "opt-in for existing customers). Never hide who you are, give a valid address for "
        "opt-outs and keep a do-not-email list. Named addresses are also personal data under "
        "UK GDPR.",
        (
            "https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/",
            _dla("GB"),
        ),
    ),
    _eu(
        "IE",
        "S.I. 336 of 2011 (ePrivacy Regulations); GDPR",
        "Ireland needs consent to market to individuals, but business-to-business email can "
        "generally be sent unless the recipient has said it does not consent. The sender must "
        "not be disguised and an opt-out must be offered. Breaches are criminal offences with "
        "fines per message, and named addresses are also personal data under GDPR.",
        _dla("IE"),
        consent_model="opt_out",
    ),
    _rule(
        "US",
        "CAN-SPAM Act 2003 (15 U.S.C. 7701 ff.), enforced by the FTC",
        "opt_out",
        "allow_with_optout",
        "The United States does not require consent for commercial email, including business "
        "email, but every message must have truthful sender details and subject, clearly say "
        "it is an advertisement, show your valid physical postal address, and offer a "
        "free opt-out honoured within 10 business days. Never buy or harvest addresses "
        "through deception.",
        ("https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business",),
        _WITH_ADDRESS,
    ),
    _rule(
        "CA",
        "Canada's Anti-Spam Legislation (CASL, S.C. 2010, c. 23)",
        "opt_in_with_exceptions",
        _STRICT,
        "Canada requires consent before commercial email. Consent is implied only in narrow "
        "cases, such as a recent customer or enquiry, or an address the person conspicuously "
        "published (without a no-unsolicited-messages notice) where your message is relevant "
        "to their role. Messages must identify you, include a mailing address and a working "
        "unsubscribe honoured within 10 business days, and you must be able to prove consent; "
        "guessed addresses do not qualify.",
        (
            "https://laws-lois.justice.gc.ca/eng/acts/E-1.6/FullText.html",
            "https://ised-isde.canada.ca/site/canada-anti-spam-legislation/en/node/7",
        ),
        _WITH_ADDRESS[:3],
    ),
    _eu(
        "DE",
        "Act Against Unfair Competition (UWG) section 7; ePrivacy Directive Art. 13; GDPR",
        "Germany treats email advertising without the recipient's prior express consent as an "
        "unacceptable nuisance, and this applies to business recipients too; the only "
        "exception is for your own customers whose address you got through a sale. Concealing "
        "the sender or having no valid opt-out address is also unlawful. Do not send cold "
        "email to Germany without documented consent.",
        "https://www.gesetze-im-internet.de/englisch_uwg/englisch_uwg.html",
        consent_model="opt_in_with_exceptions",
        default_policy=_STRICT,
    ),
    _eu(
        "FR",
        "Postal and Electronic Communications Code Art. L. 34-5 (CNIL guidance); GDPR",
        "France requires consent for consumers, but allows email to professionals about their "
        "own professional activity if they were told at collection that their address would be "
        "used for marketing and can object easily. Every message must name the advertiser and "
        "offer an opt-out. Generic company addresses are outside the consent rules.",
        _dla("FR"),
        consent_model="opt_out",
    ),
    _eu(
        "NL",
        "Telecommunicatiewet (Telecommunications Act) Art. 11.7; GDPR",
        "The Netherlands bans unsolicited commercial email unless the sender can show prior "
        "consent. Business recipients are exempt only if they published the address for the "
        "purpose of receiving such messages, so a guessed or scraped work address does not "
        "qualify. Offer a free opt-out and keep proof of consent.",
        "https://wetten.overheid.nl/BWBR0009950/",
        default_policy=_STRICT,
    ),
    _eu(
        "BE",
        "Code of Economic Law Book XII (e-commerce); GDPR",
        "Belgium requires prior, informed consent for electronic marketing, with exceptions for "
        "existing customers and for messages to legal persons such as a general company "
        "address, both under strict conditions. Every message must say how to opt out and "
        "allow doing so electronically. Named personal addresses are also personal data.",
        _dla("BE"),
    ),
    _eu(
        "ES",
        "Law 34/2002 (LSSI) Art. 20-21; GDPR and LOPDGDD",
        "Spain prohibits advertising email that the recipient has not requested or expressly "
        "authorised, and the rule covers business recipients too; the exception is for existing "
        "customers and similar products. Messages must be clearly identifiable as commercial, "
        "name the sender and include a valid address for opting out. Do not send cold email "
        "to Spain without consent.",
        "https://www.boe.es/buscar/act.php?id=BOE-A-2002-13758",
        _dla("ES"),
        default_policy=_STRICT,
    ),
    _eu(
        "IT",
        "Privacy Code (Legislative Decree 196/2003) section 130; GDPR",
        "Italy requires prior informed consent (opt-in) for marketing email, recorded with its "
        "date, with a soft exception for existing customers and similar products. The sources "
        "checked did not settle how it treats company addresses, so treat consent as the safer "
        "default. Include a simple way to opt out in every message.",
        _dla("IT"),
    ),
    _eu(
        "SE",
        "Marketing Act (2008:486) section 19; GDPR",
        "Sweden requires prior consent for automated marketing email to individuals, with an "
        "exception for existing customers and similar products. Business recipients are less "
        "restricted, but every message needs a valid address to stop future marketing. Named "
        "addresses are also personal data under GDPR.",
        _dla("SE"),
        consent_model="opt_out",
    ),
    _eu(
        "DK",
        "Marketing Practices Act (Danish); GDPR",
        "Denmark requires the recipient's prior consent for direct marketing by email, with an "
        "exception for existing customers and similar products offered an easy opt-out. The "
        "source checked describes the consent rule for natural persons, so how company "
        "recipients are treated should be confirmed. Never conceal the sender.",
        _dla("DK"),
    ),
    _eu(
        "NO",
        "Marketing Control Act section 15; GDPR",
        "Norway requires prior consent for marketing email to individuals, with an exception for "
        "existing customers and similar products; company recipients are not covered by the "
        "consent rule. Unsolicited marketing email should state clearly that it is marketing, "
        "must not hide the sender and must offer a free opt-out.",
        _dla("NO"),
        consent_model="opt_out",
    ),
    _eu(
        "FI",
        "Act on Electronic Communications Services; GDPR",
        "Finland requires prior consent for direct marketing email to individuals, but you may "
        "market to companies unless they have forbidden it. Everyone must be able to opt out "
        "easily and free of charge, and you must tell them how.",
        _dla("FI"),
        consent_model="opt_out",
    ),
    _rule(
        "CH",
        "Federal Act Against Unfair Competition (UCA) Art. 3(1)(o)",
        "opt_in_with_exceptions",
        _STRICT,
        "Switzerland requires prior consent for mass advertising by email, and the rule applies "
        "to business recipients as well as consumers; the only exception is for your own "
        "customers and similar products offered an easy opt-out. Messages must show your "
        "correct name, address and email contact and a free opt-out. Do not send cold email "
        "to Switzerland without consent.",
        (_dla("CH"),),
        _BASE_ELEMENTS,
    ),
    _eu(
        "AT",
        "Telecommunications Act 2021 (TKG) section 174; GDPR",
        "Austria prohibits email for direct marketing without the recipient's prior consent, "
        "and the government reads this as covering business recipients too; the exception is "
        "for your own customers and similar products with an easy opt-out. Do not send cold "
        "email to Austria without consent.",
        "https://www.ris.bka.gv.at/eli/bgbl/i/2021/190/P174/NOR40238632",
        "https://www.bmf.gv.at/en/topics/Telecom-and-Postal-Services/Telecommunications-Law-and-Policy/Unsolicited-messages.html",
        _dla("AT"),
        default_policy=_STRICT,
    ),
    _eu(
        "PL",
        "Electronic Communications Act Art. 398; GDPR",
        "Poland prohibits sending unsolicited commercial information, including direct "
        "marketing, to a subscriber or end user by telecommunication equipment without prior "
        "consent, and the rule is not limited to consumers. Consent must be explicit and "
        "cannot be pre-ticked. Do not send cold email to Poland without consent.",
        _dla("PL"),
        default_policy=_STRICT,
    ),
    _eu(
        "PT",
        "Law 41/2004 (as amended); GDPR",
        "Portugal requires prior consent for marketing email to individuals, but allows email "
        "to companies as long as an opt-out is offered and the company's request to be added "
        "to the non-subscribers list is respected. Never disguise the sender and always "
        "include a valid way to stop the messages.",
        _dla("PT"),
        consent_model="opt_out",
    ),
    # --- Americas ---
    _rule(
        "BR",
        "General Data Protection Law (LGPD); Consumer Defence Code",
        "opt_out",
        "warn",
        "Brazil has no specific email marketing law; the data protection law needs a lawful "
        "basis (consent or legitimate interest) to use contact data, and you must stop when "
        "someone asks. Identify yourself, keep messages truthful and include an easy opt-out.",
        (_dla("BR"),),
    ),
    _rule(
        "MX",
        "Federal Law on Protection of Personal Data Held by Private Parties (LFPDPPP)",
        "opt_out",
        "warn",
        "Mexico treats email marketing as personal data processing, so you need to give a "
        "privacy notice and honour the right to object. There is no separate anti-spam statute "
        "in the sources checked. Identify yourself and include an easy opt-out.",
        (_dla("MX"),),
    ),
    # --- Africa ---
    _rule(
        "ZA",
        "Protection of Personal Information Act 2013 (POPIA) section 69",
        "opt_in_with_exceptions",
        _STRICT,
        "South Africa prohibits direct marketing by email unless the person has consented or is "
        "your customer; this covers companies as well as individuals. You may approach someone "
        "only once to ask for consent, in the prescribed form, and customers must be offered "
        "a free opt-out each time. Do not send cold email to South Africa without consent.",
        (
            "https://popia.co.za/section-69-direct-marketing-by-means-of-unsolicited-electronic-communications/",
            _dla("ZA"),
        ),
    ),
    _rule(
        "NG",
        "Nigeria Data Protection Act 2023; NCC regulations (telemarketing)",
        "opt_out",
        "warn",
        "Nigeria has no dedicated email spam law in the sources checked; the data protection "
        "law gives people the right to object to direct marketing at any time, after which you "
        "must stop using their data. Identify yourself and keep an easy opt-out.",
        (_dla("NG"),),
    ),
    _rule(
        "KE",
        "Data Protection Act 2019 section 37; Data Protection (General) Regulations 2021",
        "opt_in_with_exceptions",
        "warn",
        "Kenya allows personal data to be used for direct marketing only with consent, or where "
        "it was collected from the person, they were told about marketing and a simple opt-out "
        "exists with no opt-out request made. Data scraped from elsewhere does not fit this, so "
        "treat consent as the safer default and honour opt-outs.",
        (_dla("KE"),),
    ),
]

RULES: dict[str, EmailRule] = {r.country_iso2: r for r in _RULE_LIST}

_UNKNOWN_SUMMARY = (
    "No researched rule is stored for this country. Treat cold email as needing the "
    "recipient's consent unless you have checked local law, identify yourself clearly and "
    "include a working unsubscribe. " + DISCLAIMER
)


def rule_for(iso2: str) -> EmailRule:
    """Return the rule for a country code; an unknown country gets a conservative `warn` rule."""
    code = (iso2 or "").strip().upper()
    rule = RULES.get(code)
    if rule is not None:
        return rule
    return EmailRule(
        country_iso2=code or "??",
        regime="Unknown (no researched rule)",
        consent_model="opt_in_with_exceptions",
        required_elements=_WITH_ADDRESS,
        subject_label=None,
        default_policy="warn",
        summary=_UNKNOWN_SUMMARY,
        sources=(),
        checked_on="",
    )


def send_gate(country_codes: Iterable[str]) -> list[EmailRule]:
    """Rules the user must acknowledge before sending to these countries (deduplicated).

    Accepts ISO codes or any name/alias the country table knows; order of first appearance is kept.
    """
    out: list[EmailRule] = []
    seen: set[str] = set()
    for item in country_codes:
        try:
            code = countries.get(item).iso2
        except countries.UnknownCountryError:
            code = (item or "").strip().upper()
        if code in seen:
            continue
        seen.add(code)
        out.append(rule_for(code))
    return out
