import logging

logger = logging.getLogger(__name__)

class BaseResource:

    def __init__(self, unifi, site, endpoint, **kwargs):
        logger.debug(f"Initializing {self.__class__.__name__} for endpoint: {endpoint}")
        self.unifi = unifi
        self.endpoint: str = endpoint
        self.data: dict = {}  # Dict that contains all the info about this resource.
        self._id: int = None  # The resource ID
        self.name: str = kwargs.get('name', None)
        self.site = site
        self.base_path: str = kwargs.get('base_path', None)
        self.api_path: str = kwargs.get('api_path', None)
        logger.debug(f"Initialized {self.__class__.__name__} with name: {self.name}, site: {self.site.name if self.site else 'None'}")

    def __str__(self):
        return f"{self.__class__.__name__}: {self.name}"

    def __repr__(self):
        return f"{self.__class__.__name__}(endpoint={self.endpoint!r}, _id={self._id!r})"

    def __eq__(self, other):
        return self._id == other._id

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value: str):
        if value:
            if not isinstance(value, str):
                raise ValueError(f'The attribute [name] must be of type str, not {type(value)}.')
        self._name = value

    def _build_url(self, item_id=None, path=None):
        site_name = getattr(self.site, "api_id", self.site.name)
        parts = [self.api_path, site_name]
        if self.base_path:
            parts.append(self.base_path)
        parts.append(self.endpoint)
        if path:
            parts.append(path)
        elif item_id:
            parts.append(str(item_id))
        normalized = [str(part).strip("/") for part in parts if part is not None]
        return "/" + "/".join(normalized)

    @staticmethod
    def _extract_response_data(response):
        if response is None:
            return None
        if isinstance(response, dict):
            status_code = response.get("statusCode")
            if isinstance(status_code, int) and status_code >= 400:
                return None
            meta = response.get("meta")
            if isinstance(meta, dict):
                if meta.get("rc") == "ok":
                    return response.get("data", {})
                return None
            if "data" in response:
                return response.get("data")
            return response
        return response

    @staticmethod
    def _response_error_message(response):
        if isinstance(response, dict):
            meta = response.get("meta", {})
            if isinstance(meta, dict) and meta.get("msg"):
                return meta.get("msg")
            if response.get("message"):
                return response.get("message")
        return "Unknown API error"

    def get(self, **filters):
        """
        Fetches and returns a single resource from the API based on the specified filters. The method
        retrieves all items available through the API endpoint and filters them according to the given
        parameters. If no items match the filters or if more than one item matches, an error is raised.

        :param filters: Key-value arguments representing the filters to apply to the API response.
                        The filters should match specific attributes of the resources.
        :type filters: dict
        :return: An instance of the class initialized with the data of the matching resource.
        :rtype: object
        :raises ValueError: When the resource retrieval fails or if the filters result in either no
                            matching resources or multiple matches.
        """
        logger.debug(f"Getting {self.endpoint} with filters: {filters}")
        items_data = self.all()
        matching_items = []
        for item in items_data:
            if all(item.get(key) == value for key, value in filters.items()):
                matching_items.append(item)
                logger.debug(f"Found matching item: {item.get('name', item.get('_id', item.get('id', 'unknown')))}")

        logger.debug(f"Found {len(matching_items)} matching items for filters: {filters}")
        if len(matching_items) == 0:
            logger.warning(f"No resource found for filters: {filters}")
            raise ValueError(f"No resource found for filters: {filters}")
        if len(matching_items) > 1:
            logger.warning(f"Multiple resources ({len(matching_items)}) found for filters: {filters}")
            raise ValueError(
                f"Multiple resources found for filters: {filters}. Filters must return exactly one result.")

        data = matching_items[0]
        logger.debug(f"Creating instance with data: {data.get('name', data.get('_id', data.get('id', 'unknown')))}")
        instance = self.__class__(self.unifi, self.site, **data)
        instance._id = data.get("_id") or data.get("id")
        instance.name = data.get("name", None)
        instance.data = data
        logger.debug(f"Successfully retrieved {self.endpoint} with ID: {instance._id}")
        return instance

    def all(self, filter_query=None, limit=200) -> list:
        """
        Fetches all available items from the endpoint.

        This method constructs the request URL using the attributes of the class,
        sends a GET request to retrieve data from the specified endpoint, and
        returns the items if the response indicates success. If the response
        does not indicate success, an empty list is returned.

        :return: A list of items retrieved from the endpoint.
        :rtype: list
        """
        logger.debug(f"Fetching all items from endpoint: {self.endpoint}")
        url = self._build_url()
        logger.debug(f"Constructed URL for all items: {url}")

        if getattr(self.unifi, "api_style", None) == "integration":
            offset = 0
            all_items = []
            while True:
                params = {"offset": offset, "limit": limit}
                if filter_query:
                    params["filter"] = filter_query
                response = self.unifi.make_request(url, "GET", params=params)
                if not isinstance(response, dict):
                    logger.error(f"Could not get data for {self.endpoint}.")
                    return []
                batch = self._extract_response_data(response)
                if not isinstance(batch, list):
                    logger.error(f"Unexpected response shape for {self.endpoint}: {response}")
                    return []
                all_items.extend(batch)
                logger.debug(f"Retrieved {len(batch)} items at offset {offset} for {self.endpoint}")
                if not batch:
                    break
                offset += len(batch)
                total_count = response.get("totalCount")
                if isinstance(total_count, int) and offset >= total_count:
                    break
                if len(batch) < response.get("limit", limit):
                    break
            logger.debug(f"Retrieved total {len(all_items)} items from {self.endpoint}")
            return all_items

        response = self.unifi.make_request(url, "GET")
        data = self._extract_response_data(response)
        if isinstance(data, list):
            logger.debug(f"Retrieved {len(data)} items from {self.endpoint}")
            return data
        if data is None:
            logger.error(f"Failed to retrieve all items: {self._response_error_message(response)}")
            return []
        logger.debug(f"Retrieved non-list data from {self.endpoint}, normalizing to single-item list")
        return [data]

    def create(self, data: dict = None):
        """
        Creates a new resource using the provided data, or default data if none is
        explicitly supplied. This method constructs the appropriate API endpoint
        URL using the site's name and other instance-specific attributes, then sends
        a POST request to the URL with the given data. If the API call is successful,
        it logs a success message and returns the created resource's data. If the
        request fails, it logs an error message and returns None.

        :param data: The data payload to send in the POST request. Defaults to
            the instance's existing `data` attribute if not explicitly provided.
            If both are absent, a `ValueError` is raised.
        :type data: dict, optional
        :return: Data of the created resource if the request is successful, or None
            otherwise.
        :rtype: dict or None
        :raises ValueError: If no data is provided to create the resource.
        """
        if not data:
            data = self.data
        if not data:
            raise ValueError(f'No data to create {self.endpoint}.')
        url = self._build_url()
        response = self.unifi.make_request(url, 'POST', data=data)
        response_data = self._extract_response_data(response)
        if response_data is not None:
            logger.info(f"Successfully created {self.endpoint} at site '{self.site.desc}'")
            return response_data
        logger.error(f"Failed to create {self.endpoint}: {self._response_error_message(response)}")
        return None

    def update(self, data: dict = None, path: str = None):
        if not data:
            data = self.data
        if not data:
            raise ValueError(f'No data to create {self.endpoint}.')
        item_path = path if path else self._id
        if not item_path:
            raise ValueError(f'No ID available to update {self.endpoint}.')
        url = self._build_url(path=item_path)
        response = self.unifi.make_request(url, 'PUT', data=data)
        response_data = self._extract_response_data(response)
        if response_data is not None:
            logger.info(f"Successfully updated {self.endpoint} with ID {item_path} at site '{self.site.desc}'")
            return response_data
        logger.error(f"Failed to update {self.endpoint} with ID {item_path}: {self._response_error_message(response)}")
        return None

    def delete(self, item_id: int = None):
        """
        Delete an item from a specific endpoint using its ID. This method sends a DELETE request
        to the appropriate URL and logs the success of the deletion operation.

        :param item_id: The ID of the item to delete. If omitted, attempts to use
                        the _id attribute of the object.
        :type item_id: int, optional

        :return: The response data from the delete operation if successful.
        :rtype: dict

        :raises ValueError: If no `item_id` is provided and the `_id` attribute is also not set.
        """
        if not item_id:
            item_id = self._id
        if not item_id:
            raise ValueError(f'Item ID required to delete {self.endpoint}.')
        url = self._build_url(item_id=item_id)
        response = self.unifi.make_request(url, 'DELETE')
        response_data = self._extract_response_data(response)
        if response_data is not None or response == {}:
            logger.info(f"Successfully deleted {self.endpoint} with ID {item_id} at site '{self.site.name}'")
            return True
        logger.error(f"Failed to delete {self.endpoint} with ID {item_id} at site {self.site.name}: {self._response_error_message(response)}")
        return False
