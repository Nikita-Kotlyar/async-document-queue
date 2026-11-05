from __future__ import annotations


class ProcessingError(Exception):
    code = "processing_error"


class TransientProcessingError(ProcessingError):
    """A temporary failure: the task should be retried."""

    code = "transient_error"


class PermanentProcessingError(ProcessingError):
    """A permanent failure: retrying cannot help; send to dead-letter."""

    code = "permanent_error"


class UnsupportedDocumentError(PermanentProcessingError):
    code = "unsupported_document"


class CorruptDocumentError(PermanentProcessingError):
    code = "corrupt_document"


class OcrUnavailableError(TransientProcessingError):
    code = "ocr_unavailable"


class WebhookTransientError(TransientProcessingError):
    code = "webhook_transient_error"
