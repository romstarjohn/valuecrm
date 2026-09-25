from django.conf import settings


def branding(request):
    """
    Exposes the configurable brand/business names (see AGENT.md) to every
    template, so no template hardcodes "ValuedCRM" or any other name.
    """
    return {
        "BRAND_NAME": settings.BRAND_NAME,
        "BUSINESS_NAME": settings.BUSINESS_NAME,
    }
