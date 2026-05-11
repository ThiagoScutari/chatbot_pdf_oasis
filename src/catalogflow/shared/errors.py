"""Hierarquia de exceções de domínio do CatalogFlow.

Todas as exceções de negócio derivam de `DomainError`. Os handlers globais
em `catalogflow.main` (Fase C) mapeiam cada subclasse para um envelope HTTP
estável (`code`, `status`, `message`).
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Exceção base para erros de domínio.

    Atributos:
        code: identificador estável usado no envelope JSON (ex: `BRAND_NOT_FOUND`).
        message: mensagem human-readable em português.
        details: payload arbitrário para diagnóstico (incluído no envelope).
    """

    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}


# ──────────────────────────────────────────────
#  Erros transversais
# ──────────────────────────────────────────────


class NotFoundError(DomainError):
    """Recurso não existe ou não pertence à brand autenticada."""

    code = "NOT_FOUND"
    http_status = 404


class ConflictError(DomainError):
    """Violação de unicidade ou estado conflitante (ex: slug já existe)."""

    code = "CONFLICT"
    http_status = 409


class ValidationError(DomainError):
    """Entrada não passa em regra de domínio (além da validação Pydantic)."""

    code = "VALIDATION_ERROR"
    http_status = 422


# ──────────────────────────────────────────────
#  Erros de autenticação / autorização
# ──────────────────────────────────────────────


class AuthenticationError(DomainError):
    """Credencial ausente, inválida ou expirada."""

    code = "AUTHENTICATION_FAILED"
    http_status = 401


class AuthorizationError(DomainError):
    """Credencial válida mas sem permissão para a operação."""

    code = "FORBIDDEN"
    http_status = 403


# ──────────────────────────────────────────────
#  Stubs para fases seguintes (implementação parcial aqui)
# ──────────────────────────────────────────────


class PDFEncryptedError(DomainError):
    """PDF de entrada está protegido por senha."""

    code = "PDF_ENCRYPTED"
    http_status = 400


class PDFCorruptError(DomainError):
    """PDF de entrada é inválido ou corrompido."""

    code = "PDF_CORRUPT"
    http_status = 400


class PDFTooLargeError(DomainError):
    """Upload excede `MAX_PDF_SIZE_MB`."""

    code = "FILE_TOO_LARGE"
    http_status = 400


class PDFNoProductsError(DomainError):
    """PDF não contém páginas de produto identificáveis."""

    code = "PDF_NO_PRODUCTS"
    http_status = 422


class PDFFlattenedError(DomainError):
    """PDF perdeu os campos AcroForm (impresso como PDF)."""

    code = "PDF_FLATTENED"
    http_status = 422


class JobNotReadyError(DomainError):
    """Recurso solicitado ainda está em processamento."""

    code = "NOT_READY"
    http_status = 409
