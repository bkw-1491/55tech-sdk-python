from fiftyfivetech.connection import FiftyFiveTechConnection


class Endpoints:
    """
    Base for the endpoint groups: holds the ``FiftyFiveTechConnection`` they send through.

    :param conn: Connection to send through.
    """

    def __init__(self, conn: FiftyFiveTechConnection) -> None:
        """
        Create the group; see the class docstring for parameters.
        """
        self.conn = conn
