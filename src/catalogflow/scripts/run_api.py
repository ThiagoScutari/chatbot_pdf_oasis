"""Entry point do servidor API (uvicorn programático).

Registrado em `[project.scripts]` como `catalogflow-api`. Em produção,
prefira invocar `uvicorn catalogflow.main:app` diretamente — este script
serve como atalho em desenvolvimento.
"""

from __future__ import annotations


def main() -> None:
    """Sobe o servidor Uvicorn apontando para `catalogflow.main:app`.

    Implementação completa entra na Fase C (Prompt 3), quando `main:app`
    existir. Até lá, este stub apenas instrui o usuário.
    """
    raise NotImplementedError(
        "catalogflow.main:app ainda não existe — entra na Fase C da Sprint 01."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
