"""aggplan — calculation, validation, persistence, export and report modules for
Juicetification: Aggregate Anxiety. Nothing in this package imports Streamlit at
module load (student_store/identity touch it lazily), so it is unit-testable."""

from manifest import MODEL_VERSION  # single source of truth for the model version

APP_NAME = "Juicetification: Aggregate Anxiety"
__all__ = ["MODEL_VERSION", "APP_NAME"]
