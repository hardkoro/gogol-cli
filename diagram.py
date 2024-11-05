"""CLI diagram."""

from diagrams import Cluster, Diagram, Edge
from diagrams.programming.language import Python
from diagrams.onprem.database import Mysql

with Diagram("CLI", show=False):
    cli = Python("CLI")
    db_client = Python("Database Client")
    database = Mysql("External Database")

    exporter = Python("Exporter")

    with Cluster("Exporters"):
        plain_exporter = Python("Plain Exporter")
        smtp_exporter = Python("SMTP Exporter")

    db_client >> Edge(label="1. Get DB data") >> cli
    database >> Edge(label="2. Fetch DB data") >> db_client
    db_client >> Edge(label="3. Return DB data") >> database
    cli >> Edge(label="4. Respond with DB data") >> db_client
    cli >> Edge(label="5. Export DB data") >> exporter

    plain_exporter >> Edge(style="dashed", label="6. Export as plain text") >> exporter
    smtp_exporter >> Edge(style="dashed", label="6. Export as email") >> exporter
