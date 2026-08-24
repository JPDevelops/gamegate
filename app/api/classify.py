from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_event_repo
from app.security import require_api_token
from app.services.classifier import Classification, SafeClassifier, build_classifier
from app.services.repositories import EventRepository

router = APIRouter(dependencies=[Depends(require_api_token)])

EventRepoDep = Annotated[EventRepository, Depends(get_event_repo)]
ClassifierDep = Annotated[SafeClassifier, Depends(build_classifier)]


@router.post(
    "/events/{event_id}/classify",
    response_model=Classification,
)
def classify_event(
    event_id: str, repo: EventRepoDep, classifier: ClassifierDep
) -> Classification:
    event = repo.find_by_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Unknown event")
    return classifier.classify(event)
