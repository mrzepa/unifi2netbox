from unifi.resources import BaseResource
import logging
logger = logging.getLogger(__name__)

class Device(BaseResource):
    BASE_PATH = 'stat'
    API_PATH = "/api/s"
    INTEGRATION_API_PATH = "/sites"
    INTEGRATION_ENDPOINT = "devices"

    def __init__(self, unifi, site, **kwargs):
        self.unifi = unifi
        self.site = site
        if getattr(unifi, "api_style", None) == "integration":
            super().__init__(
                unifi,
                site,
                endpoint=self.INTEGRATION_ENDPOINT,
                api_path=self.INTEGRATION_API_PATH,
                base_path=None,
                **kwargs,
            )
        else:
            super().__init__(
                unifi,
                site,
                endpoint='device',
                api_path=self.API_PATH,
                base_path=self.BASE_PATH,
                **kwargs,
            )
