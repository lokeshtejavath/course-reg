"""Load demo learners and offerings from a JSON seed file.

Seeding is insert-if-missing (learners matched by username, offerings by code),
so restarting the app never overwrites existing rows. Learner UUIDs are generated.
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
            if session.scalar(select(Learner.id).where(Learner.username == item["username"])) is not None:
                continue
            codes = {normalize_course_code(c) for c in item.get("completed_courses", [])}
            session.add(
                Learner(
                    username=item["username"],
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
