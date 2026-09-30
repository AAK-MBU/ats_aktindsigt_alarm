"""Opslag af konstanter og credentials i RPA-databasen.

Går gennem ``mbu_rpa_core.database.connection.RPAConnection``, som læser
``[rpa].[Constants]`` og ``[rpa].[Credentials]`` via forbindelsesstrengen i
miljøvariablen ``DBCONNECTIONSTRINGPROD``. ``RPAConnection`` importeres først
ved kaldet, da den trækker pyodbc og dermed en ODBC-driver med sig.
"""


def get_constant(name: str) -> str:
    """Returnerer værdien af en konstant i rpa.Constants.

    Args:
        name: Konstantens navn.

    Returns:
        Konstantens værdi.
    """
    from mbu_rpa_core.database.connection import RPAConnection  # noqa: PLC0415

    with RPAConnection(db_env="PROD", commit=False) as conn:
        return conn.get_constant(name)["value"]


def get_credential_password(name: str) -> str:
    """Returnerer det dekrypterede password for en credential i rpa.Credentials.

    Args:
        name: Credentialens navn.

    Passwordene bruges som API-nøgler i HTTP-headers, så mellemrum og
    linjeskift i enderne, fx fra copy-paste, fjernes. requests afviser ellers
    headeren med ``InvalidHeader``.

    Returns:
        Det dekrypterede password uden mellemrum og linjeskift i enderne.
    """
    from mbu_rpa_core.database.connection import RPAConnection  # noqa: PLC0415

    with RPAConnection(db_env="PROD", commit=False) as conn:
        return conn.get_credential(name)["decrypted_password"].strip()
