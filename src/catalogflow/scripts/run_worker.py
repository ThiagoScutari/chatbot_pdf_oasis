"""Entry point do worker Celery.

Registrado em `[project.scripts]` como `catalogflow-worker`. Prefira invocar
`celery -A catalogflow.infra.celery_app worker` diretamente — este script
serve como atalho em desenvolvimento.
"""

from __future__ import annotations


def main() -> None:
    """Inicia um worker Celery apontando para `catalogflow.infra.celery_app`.

    Implementação completa entra na Fase D (Prompt 5), quando `celery_app`
    existir. Até lá, este stub apenas instrui o usuário.
    """
    raise NotImplementedError(
        "catalogflow.infra.celery_app ainda não existe — entra na Fase D da Sprint 01."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
