"""Synthetic job titles with the expected designation group and seniority.

Each case is (title, expected_group_key_or_"other", expected_seniority).
Group keys are the TEMPLATE_DATA keys in services/designation_templates.py.
Seniority levels: individual < manager < director < vp < c_suite.

Base rules not spelled out in the task card (confirmed against PLAN section 6 by the builder):
plain "Manager" = manager and plain "Director" = director; any title with no rank word is
individual. Cases that rely on them are marked "base rule" in their comment.

Every case matches at most one built-in template (checked by tests/test_designations.py), so the
expected group does not depend on template order. "Facilities Manager" is left out on purpose:
it matches both safety_ops ("facilit*") and facilities.

All people and companies are invented. Some company names after "at" or "@"
deliberately contain group keywords (for example "Facility", "Training") to test
that only the role part of a title is classified.
"""

TITLE_CASES: list[tuple[str, str, str]] = [
    # --- L&D / Training (ld) ---
    ("Learning Manager", "ld", "manager"),  # base rule: plain Manager
    ("Head of Learning", "ld", "vp"),  # "Head of" = vp
    ("Training Manager@Example Utility", "ld", "manager"),  # role part before "@"
    ("L&D Manager", "ld", "manager"),  # "L&D" kept as one token
    ("L & D Director", "ld", "director"),  # spaced "L & D" = l&d
    ("L and D Manager", "ld", "manager"),  # "L and D" = l&d
    ("Learning & Development Manager", "ld", "manager"),  # "&" splits words
    ("Head, Learning and Development", "ld", "vp"),  # "Head," = vp
    ("Head of Organisational Development", "ld", "vp"),  # multi-word keyword
    ("Employee Development Manager", "ld", "manager"),  # added keyword "employee development"
    ("Staff Development Coordinator", "ld", "individual"),  # new keyword; Coordinator = individual
    ("Instructional Design Manager", "ld", "manager"),  # keyword "instructional"
    ("eLearning Manager", "ld", "manager"),  # keyword "elearning"
    ("AVP, Learning", "ld", "vp"),  # "AVP" = vp
    ("Retraining Coordinator", "other", "individual"),  # whole word: "retraining" is not "training"
    # --- HR leaders (hr) ---
    ("HR Manager", "hr", "manager"),  # base rule: plain Manager
    ("HR Business Partner", "hr", "manager"),  # "HR Business Partner" = manager
    ("Senior HR Business Partner", "hr", "manager"),  # "HR Business Partner" = manager
    ("Head of HR at Example Foods", "hr", "vp"),  # role part before " at "
    ("Human Resources Director", "hr", "director"),  # "human resource*" prefix matches "resources"
    ("Vice President, Human Resources", "hr", "vp"),  # "vice president" is vp, not owners
    ("Senior Vice President, Human", "other", "vp"),  # "human" alone does not match
    ("Recruitment Manager", "hr", "manager"),  # "recruit*" prefix
    ("Junior Recruiter", "hr", "individual"),  # "recruit*" prefix; "Junior" = individual
    ("Talent Acquisition Partner", "hr", "individual"),  # "Partner" alone does not count
    ("People Partner", "hr", "individual"),  # "People Partner" = individual
    ("Talent Acquisition Partner Manager", "hr", "manager"),  # "Manager" overrides Partner rule
    ("Chief People Officer", "hr", "c_suite"),  # "Chief" wins over "Officer" rule
    ("CHRO, Example Foods", "hr", "c_suite"),  # "CHRO" = c_suite
    ("Employee Experience Manager", "hr", "manager"),  # keyword "employee experience"
    ("HR Intern", "hr", "individual"),  # "Intern" = individual
    # --- Safety / Operations (safety_ops) ---
    ("Operations Manager", "safety_ops", "manager"),  # "operation*" prefix
    ("Operations Director", "safety_ops", "director"),  # "operation*" prefix
    ("Head of Operations at Example Logistics", "safety_ops", "vp"),  # role part; company ignored
    ("HSE Manager", "safety_ops", "manager"),  # keyword "hse"
    ("Quality Manager", "safety_ops", "manager"),  # keyword "quality"
    ("Site Manager", "safety_ops", "manager"),  # multi-word keyword "site manager"
    ("Compliance Officer", "safety_ops", "individual"),  # "Officer" without Chief = individual
    ("Vice President, Operations", "safety_ops", "vp"),  # not owners: "vice president" is vp
    ("Vice-President Operations", "safety_ops", "vp"),  # hyphen variant of "vice president"
    # "IT operations" drops only "operations", so these are IT, not Operations.
    ("Head of IT Operations", "it", "vp"),
    ("IT Operations Manager", "it", "manager"),
    ("IT Infrastructure Operations Manager", "it", "manager"),
    ("Network Operations Director", "other", "director"),  # false friend: "network operations"
    ("Cloud Operations Manager", "other", "manager"),  # false friend: "cloud operations"
    ("Security Operations Manager", "other", "manager"),  # false friend: "security operations"
    ("Cyber Operations Manager", "other", "manager"),  # false friend: "cyber operations"
    ("Data Operations Manager", "other", "manager"),  # false friend: "data operations"
    # "network operations" loses "operations", which leaves "IT Network Manager" (a real IT role).
    ("IT Network Operations Manager", "it", "manager"),
    # --- Business owners / MDs (owners) ---
    ("CEO at Example Facility Services", "owners", "c_suite"),  # role part; "Facility" is company
    ("Founder, Northwind Logistics", "owners", "c_suite"),  # "Founder" = c_suite
    ("Co-Founder at Example Coffee Roasters", "owners", "c_suite"),  # hyphen: "co-founder" keyword
    ("Owner at Example Dental Clinic", "owners", "c_suite"),  # "Owner" = c_suite
    ("President, Example Foods", "owners", "c_suite"),  # "President" (not vice) = c_suite
    ("President and CEO at Example Medical", "owners", "c_suite"),  # c_suite via CEO/President
    ("Managing Director", "owners", "vp"),  # "Managing Director" is a rank = vp
    ("Managing Director, Northwind Group", "owners", "vp"),  # "Group" alone is not enough
    ("Group Managing Director", "owners", "vp"),  # "Group" without "CEO" = vp
    ("Group CEO and Managing Director", "owners", "c_suite"),  # "Group" + "CEO" = c_suite
    ("Managing Partner at Example Legal", "owners", "c_suite"),  # "Managing Partner" = c_suite
    ("Senior Partner", "other", "c_suite"),  # "Senior Partner" = c_suite; no group keyword
    ("Equity Partner", "other", "c_suite"),  # "Equity Partner" = c_suite; no group keyword
    # --- Marketing (marketing) ---
    ("Marketing Manager", "marketing", "manager"),  # base rule: plain Manager
    ("Head of Marketing at Example Training Ltd", "marketing", "vp"),  # company "Training" ignored
    ("Chief Marketing Officer", "marketing", "c_suite"),  # "Chief" = c_suite
    ("Digital Marketing Director", "marketing", "director"),  # keyword "marketing"
    ("Demand Generation Manager", "marketing", "manager"),  # multi-word keyword
    ("SEO Manager", "marketing", "manager"),  # keyword "seo"
    ("Brand Manager", "marketing", "manager"),  # multi-word keyword "brand manager"
    # --- Sales (sales) ---
    ("Sales Director", "sales", "director"),  # keyword "sales"
    ("Business Development Manager", "sales", "manager"),  # multi-word keyword
    ("Account Executive", "sales", "individual"),  # "Executive" alone = individual
    ("Sales Trainee", "sales", "individual"),  # "Trainee" is not an L&D word
    ("Executive Vice President, Sales", "sales", "vp"),  # "Executive" ignored with "Vice President"
    ("EVP Sales", "sales", "vp"),  # "EVP" = vp
    ("Chief Revenue Officer", "sales", "c_suite"),  # multi-word keyword; "Chief" = c_suite
    ("Commercial Director", "sales", "director"),  # keyword "commercial director"
    # --- IT / Technology leaders (it) ---
    ("CTO at Example Software", "it", "c_suite"),  # "CTO" = c_suite
    ("IT Director", "it", "director"),  # keyword "it"
    ("Head of Technology", "it", "vp"),  # "Head of" = vp
    ("Chief Information Officer", "it", "c_suite"),  # "Chief" = c_suite
    ("Technology Manager", "it", "manager"),  # keyword "technology"
    ("IT Manager", "it", "manager"),  # keyword "it"
    ("Director of IT", "it", "director"),  # keyword "it"
    ("Director, IT", "it", "director"),
    ("VP of IT", "it", "vp"),
    ("VP IT", "it", "vp"),
    ("VP, Information Technology", "it", "vp"),  # keyword "information technology"
    ("IT Head", "it", "vp"),  # trailing "Head" = vp
    ("IT Lead", "it", "manager"),  # "Lead" = manager
    ("Head of Information Technology", "it", "vp"),
    ("Director of Information Technology", "it", "director"),
    ("Director of Technology", "it", "director"),  # keyword "technology"
    ("IT Infrastructure Manager", "it", "manager"),  # "infrastructure" only hurts with "operations"
    ("CISO", "it", "c_suite"),
    ("Chief Information Security Officer", "it", "c_suite"),
    ("Head of Information Security", "it", "vp"),  # keyword "information security"
    # --- Finance (finance) ---
    ("CFO at Example Retail", "finance", "c_suite"),  # "CFO" = c_suite
    ("Finance Manager", "finance", "manager"),  # keyword "finance"
    ("Head of Finance", "finance", "vp"),  # "Head of" = vp
    ("Chief Financial Officer", "finance", "c_suite"),  # "Chief" = c_suite
    ("Financial Director", "finance", "director"),  # keyword "financial*" prefix
    ("Finance Officer", "finance", "individual"),  # "Officer" without Chief = individual
    ("Accounts Payable Manager", "finance", "manager"),  # multi-word keyword "accounts payable"
    # --- Procurement (procurement) ---
    ("Procurement Manager", "procurement", "manager"),  # keyword "procurement"
    ("Head of Procurement", "procurement", "vp"),  # "Head of" = vp
    ("Purchasing Specialist", "procurement", "individual"),  # "Specialist" = individual
    ("Supply Chain Director", "procurement", "director"),  # multi-word keyword "supply chain"
    ("Category Manager", "procurement", "manager"),  # keyword "category manager"
    ("Junior Buyer", "procurement", "individual"),  # "Junior" = individual
    ("Chief Procurement Officer", "procurement", "c_suite"),  # "Chief" = c_suite
    ("Supply Chain Coordinator", "procurement", "individual"),  # "Coordinator" = individual
    # --- Facilities (facilities); "Facilities Manager" avoided: also matches safety_ops ---
    ("Estates Manager", "facilities", "manager"),  # keyword "estates"
    ("Building Manager", "facilities", "manager"),  # keyword "building manager"
    ("Property Manager", "facilities", "manager"),  # keyword "property manager"
    ("Workplace Manager", "facilities", "manager"),  # keyword "workplace manager"
    ("Estates Director at Example Facilities Group", "facilities", "director"),  # role part only
    # --- Customer service (customer_service) ---
    ("Customer Success Manager", "customer_service", "manager"),  # multi-word keyword
    ("CUSTOMER SUPPORT MANAGER", "customer_service", "manager"),  # case-insensitive
    ("Head of Customer Service", "customer_service", "vp"),  # "Head of" = vp
    ("Contact Centre Manager", "customer_service", "manager"),  # keyword "contact centre"
    ("Call Center Manager", "customer_service", "manager"),  # keyword "call center"
    ("Client Services Director", "customer_service", "director"),  # keyword "client services"
    ("Helpdesk Coordinator", "customer_service", "individual"),  # "Coordinator" = individual
    ("Customer Experience Manager", "customer_service", "manager"),  # keyword "customer experience"
    # --- Role part before " at " or "@" ---
    ("Marketing Manager at Example Procurement Ltd", "marketing", "manager"),  # company ignored
    ("Software Engineering Manager at Example Training Ltd", "other", "manager"),  # company ignored
    # --- No group match, and false friends for machine/deep/reinforcement/statistical learning ---
    ("Software Engineering Manager", "other", "manager"),  # no keyword matches
    ("Vice President of Engineering", "other", "vp"),  # "vice president" = vp; no group
    ("Chief Engineer", "other", "c_suite"),  # "Chief" = c_suite; no group
    ("Machine Learning Engineering Manager", "other", "manager"),  # false friend: machine learning
    ("Deep Learning Director", "other", "director"),  # false friend: "deep learning"
    ("Reinforcement Learning Manager", "other", "manager"),  # false friend: reinforcement learning
    ("Statistical Learning Manager", "other", "manager"),  # false friend: "statistical learning"
    ("Model Training Manager", "other", "manager"),  # false friend: "model training"
    ("Quality of Life Manager", "other", "manager"),  # false friend: "quality of life"
]
