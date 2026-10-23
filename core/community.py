"""Helpers for linking users to the community group."""

from html import escape


def format_community_group_cta(group_url):
    """Build the optional Spanish call to action for the community group."""
    group_url = str(group_url or "").strip()
    if not group_url:
        return ""

    safe_url = escape(group_url, quote=True)
    return (
        "💬 <b>¿Tienes dudas o quieres más detalles?</b>\n"
        f'<a href="{safe_url}">Únete al grupo de la comunidad</a>'
    )
