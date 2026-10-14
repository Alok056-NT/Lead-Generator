# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/export.py (DESIGNATION_RULES)
"""Built-in designation (job-role group) templates: label, keywords and search terms.

Keyword rules: lower-case whole words, multi-word phrases allowed, a trailing "*"
means "starts with". The first four templates (ld, hr, safety_ops, owners) keep
Harvey's keyword lists unchanged (plus the card's additions to ld). Search terms are plain
phrases that are wrapped in quotes when a search query is built.

Some templates overlap on purpose (for example "Facilities Manager" matches both
safety_ops, through "facilit*", and facilities). In a campaign the first group in the list
wins, so put the more specific group first.
"""

TEMPLATE_DATA: dict[str, dict] = {
    "ld": {
        "label": "L&D / Training heads",
        "keywords": [
            "learning", "l&d", "training", "trainer",
            "talent development", "capability", "organisational development",
            "organizational development", "organisation development",
            "organization development", "leadership development",
            "employee development", "people development", "staff development",
            "instructional", "elearning",
        ],
        "search_terms": [
            "learning and development manager",
            "L&D manager",
            "head of learning",
            "training manager",
            "head of training",
            "talent development manager",
        ],
    },
    "hr": {
        "label": "HR leaders",
        "keywords": [
            "hr", "hrbp", "human resource*", "people", "talent", "recruit*", "culture",
            "employee experience", "chro",
        ],
        "search_terms": [
            "HR director",
            "head of HR",
            "HR manager",
            "HR business partner",
            "chief people officer",
            "people and culture director",
        ],
    },
    "safety_ops": {
        "label": "Safety / Operations",
        "keywords": [
            "safety", "ehs", "wsh", "hse", "operation*", "facilit*", "quality",
            "compliance", "maintenance", "production", "plant", "site manager",
        ],
        "search_terms": [
            "HSE manager",
            "health and safety manager",
            "EHS director",
            "operations manager",
            "site manager",
            "quality manager",
        ],
    },
    "owners": {
        "label": "Business owners / MDs",
        "keywords": [
            "ceo", "founder", "co-founder", "cofounder", "owner", "proprietor",
            "managing director", "managing partner", "md", "general manager",
            "president", "chairman", "principal", "executive director",
            "country manager", "country head",
        ],
        "search_terms": [
            "managing director",
            "founder",
            "CEO",
            "owner",
            "general manager",
            "country manager",
        ],
    },
    "marketing": {
        "label": "Marketing",
        "keywords": [
            "marketing", "cmo", "brand manager", "brand director", "head of brand",
            "growth manager", "growth director", "head of growth", "demand generation",
            "content manager", "content director", "head of content", "seo",
            "communications manager", "communications director",
        ],
        "search_terms": [
            "marketing manager",
            "head of marketing",
            "marketing director",
            "CMO",
            "demand generation manager",
            "brand manager",
        ],
    },
    "sales": {
        "label": "Sales",
        "keywords": [
            "sales", "chief revenue officer", "revenue director", "head of revenue",
            "account executive", "account manager", "business development", "bdm",
            "commercial director", "commercial manager", "head of commercial",
        ],
        "search_terms": [
            "sales director",
            "head of sales",
            "sales manager",
            "business development manager",
            "account executive",
            "chief revenue officer",
        ],
    },
    "it": {
        "label": "IT / Technology leaders",
        "keywords": [
            "cio", "cto", "chief information officer", "chief technology officer",
            "it director", "it manager", "head of it", "head of technology",
            "technology director", "technology manager",
            "information technology director", "information technology manager",
        ],
        "search_terms": [
            "IT director",
            "CIO",
            "CTO",
            "head of IT",
            "IT manager",
            "chief technology officer",
        ],
    },
    "finance": {
        "label": "Finance",
        "keywords": [
            "finance", "financial*", "cfo", "accountant", "accounting",
            "accounts payable", "accounts receivable", "treasurer", "treasury",
        ],
        "search_terms": [
            "finance director",
            "CFO",
            "financial controller",
            "finance manager",
            "head of finance",
            "group financial controller",
        ],
    },
    "procurement": {
        "label": "Procurement",
        "keywords": [
            "procurement", "purchasing", "purchaser", "buyer", "sourcing manager",
            "sourcing director", "head of sourcing", "supply chain", "category manager",
            "category director", "supplier manager", "vendor manager",
        ],
        "search_terms": [
            "procurement manager",
            "head of procurement",
            "purchasing manager",
            "sourcing manager",
            "supply chain director",
            "chief procurement officer",
        ],
    },
    "facilities": {
        "label": "Facilities",
        "keywords": [
            "facilities", "facility", "estates", "building manager", "building services",
            "property manager", "workplace manager", "workplace services", "premises",
            "campus manager",
        ],
        "search_terms": [
            "facilities manager",
            "head of facilities",
            "facilities director",
            "estates manager",
            "building manager",
            "workplace manager",
        ],
    },
    "customer_service": {
        "label": "Customer service",
        "keywords": [
            "customer service", "customer success", "customer experience",
            "customer support", "customer care", "contact centre", "contact center",
            "call centre", "call center", "client services", "client success",
            "helpdesk", "help desk",
        ],
        "search_terms": [
            "customer service manager",
            "head of customer service",
            "customer success manager",
            "customer support manager",
            "contact centre manager",
            "client services director",
        ],
    },
}
