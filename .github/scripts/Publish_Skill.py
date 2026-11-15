import os
import sys

import anthropic
from anthropic.lib import files_from_dir
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", ".claude", "skills")

SKILLS = [
    {
        "key": "company-intelligence-report",
        "dir": os.path.join(BASE_DIR, "company-intelligence-report"),
        "display_title": "Company Intelligence Report",
        "env_var": "COMPANY_SKILL_ID",
    },
    {
        "key": "photo-link-reader",
        "dir": os.path.join(BASE_DIR, "photo-link-reader"),
        "display_title": "Photo & Link Reader",
        "env_var": "PHOTO_LINK_SKILL_ID",
    },
    {
        "key": "deal-pipeline-digest",
        "dir": os.path.join(BASE_DIR, "deal-pipeline-digest"),
        "display_title": "Deal Pipeline Digest",
        "env_var": "PIPELINE_SKILL_ID",
    },
    {
        "key": "generate-doc-format",
        "dir": os.path.join(BASE_DIR, "generate-doc-format"),
        "display_title": "Generate Doc Format",
        "env_var": "DOC_FORMAT_SKILL_ID",
    },
]


requested = sys.argv[1:]
skills_to_publish = [s for s in SKILLS if not requested or s["key"] in requested]

client = anthropic.Anthropic() 


for skill in skills_to_publish:
    skill_id = os.environ.get(skill["env_var"])
    if not skill_id:
        created = client.beta.skills.create(
            display_title=skill["display_title"],
            files=files_from_dir(skill["dir"]),
            betas=["skills-2025-10-02"],
        )
        print(f"Created skill: {created.id}")
        print(f"Save this as the {skill['env_var']} secret.")
    else:
        version = client.beta.skills.versions.create(
            skill_id=skill_id,
            files=files_from_dir(skill["dir"]),
            betas=["skills-2025-10-02"],
        )
        print(f"Published new version {version.version} of {skill_id}")
