"""Errors raised by the SOW generator, mapped to the standard error body."""

from sow_generator.core.errors import (
    AppError,
    BadGatewayError,
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
)


class TemplateError(NotFoundError):
    """The requested template id is malformed, unknown or missing."""

    code = "template_not_found"
    default_message = "Template not found."


class SowInputError(BadRequestError):
    """Request inputs cannot be turned into agent inputs (for example an unreadable upload)."""

    code = "invalid_sow_input"
    default_message = "The submitted inputs could not be processed."


class ExportFailedError(AppError):
    """The document could not be rendered."""

    status_code = 500
    code = "export_failed"
    default_message = "Export failed."


class ExportStorageError(BadGatewayError):
    """The rendered document could not be stored."""

    code = "export_storage_failed"
    default_message = "Export storage failed."


class ExportStorageUnavailableError(ServiceUnavailableError):
    """Download links need storage, and none is configured."""

    code = "export_storage_unavailable"
    default_message = "Export storage is not configured."
