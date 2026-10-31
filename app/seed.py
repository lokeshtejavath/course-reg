"""Load learners (and optional demo offerings) from a JSON seed file.

Learners are managed only through this file; there is no learner API. Seeding
is insert-if-missing, so restarting the app never overwrites existing rows.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Learner, LearnerCompletedCourse, Offering, OfferingPrerequisite
from app.service import normalize_course_code


def load_seed(session_factory: sessionmaker, seed_file: Path) -> None:
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    with session_factory.begin() as session:
        for item in data.get("learners", []):
            if session.get(Learner, item["id"]) is not None:
                continue
            codes = {normalize_course_code(c) for c in item.get("completed_courses", [])}
            session.add(
                Learner(
                    id=item["id"],
                    name=item["name"],
                    completed_courses=[LearnerCompletedCourse(course_code=c) for c in sorted(codes)],
                )
            )
        for item in data.get("offerings", []):
            code = normalize_course_code(item["code"])
            if session.scalar(select(Offering.id).where(Offering.code == code)) is not None:
                continue
            prereqs = {normalize_course_code(c) for c in item.get("prerequisites", [])}
            session.add(
                Offering(
                    code=code,
                    title=item["title"],
                    capacity=item["capacity"],
                    prerequisites=[OfferingPrerequisite(course_code=c) for c in sorted(prereqs)],
                )
            )
