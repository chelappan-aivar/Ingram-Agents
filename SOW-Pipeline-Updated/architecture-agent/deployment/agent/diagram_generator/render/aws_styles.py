"""draw.io style strings for AWS reference-architecture diagrams, taken verbatim from the AWS
`aws-architecture-diagram` skill (awslabs/agent-plugins, Apache-2.0; see vendor/aws-architecture-diagram).
Used by diagram_generator/render/drawio_builder.py, which turns the Diagram coder's layout into draw.io XML."""
from __future__ import annotations

from diagram_generator.contracts import IconRef

FONT = "fontFamily=Helvetica;"

_GROUP_PREFIX = (
    "points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],"
    "[0,0.75],[0,0.5],[0,0.25]];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;shape=mxgraph.aws4.group;"
    "verticalAlign=top;align=left;spacingLeft=30;fontFamily=Helvetica;pointerEvents=0;collapsible=0;recursiveResize=0;"
)

ICON_PREFIX = (
    "sketch=0;points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],"
    "[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];outlineConnect=0;fontColor=#232F3E;"
    "strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=10;"
    "fontStyle=0;aspect=fixed;fontFamily=Helvetica;shadow=1;whiteSpace=wrap;labelWidth=112;"
)
SUB_PREFIX = (
    "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=none;dashed=0;"
    "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=10;fontStyle=0;aspect=fixed;"
    "pointerEvents=1;fontFamily=Helvetica;whiteSpace=wrap;labelWidth=112;"
)

# BoundaryType -> full group style
GROUP_STYLES: dict[str, str] = {
    "aws_cloud": _GROUP_PREFIX + "fontSize=14;fontStyle=1;grIcon=mxgraph.aws4.group_aws_cloud;strokeColor=#232F3E;"
                 "fillColor=#232F3E0D;fontColor=#232F3E;dashed=0;container=1;",
    "region": _GROUP_PREFIX + "fontSize=14;fontStyle=1;grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;"
              "fillColor=#0C7B7D0D;fontColor=#00A4A6;dashed=1;container=0;",
    "vpc": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;"
           "fillColor=#8C4FFF0D;fontColor=#8C4FFF;dashed=0;container=1;",
    "subnet_public": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_security_group;grStroke=0;"
                     "strokeColor=#248814;fillColor=#2488140D;fontColor=#248814;dashed=0;container=1;",
    "subnet_private": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_security_group;grStroke=0;"
                      "strokeColor=#147EBA;fillColor=#147EBA0D;fontColor=#147EBA;dashed=0;container=1;",
    "az": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_availability_zone;strokeColor=#147EBA;"
          "fillColor=none;fontColor=#147EBA;dashed=1;container=1;",
    "security_group": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_security_group;"
                      "strokeColor=#DD3522;fillColor=none;fontColor=#DD3522;dashed=0;container=1;",
    "account": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_account;strokeColor=#CD2264;"
               "fillColor=none;fontColor=#CD2264;dashed=0;container=1;",
    "on_prem": _GROUP_PREFIX + "fontSize=12;fontStyle=0;grIcon=mxgraph.aws4.group_corporate_data_center;"
               "strokeColor=#5A6C86;fillColor=none;fontColor=#5A6C86;dashed=0;container=1;",
}
GENERIC_GROUP = (
    "fillColor=none;strokeColor=#666666;dashed=1;rounded=0;verticalAlign=top;align=left;spacingLeft=10;"
    "fontColor=#666666;fontSize=12;fontStyle=1;html=1;whiteSpace=wrap;container=1;pointerEvents=0;collapsible=0;"
    "recursiveResize=0;fontFamily=Helvetica;"
)
AUX_GROUP = (
    "rounded=0;fillColor=none;dashed=1;strokeColor=#666666;verticalAlign=top;align=left;spacingLeft=10;fontSize=12;"
    "fontStyle=1;fontColor=#666666;html=1;whiteSpace=wrap;container=1;fontFamily=Helvetica;"
)
BOX_BASE = ("rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;fontStyle=1;fontSize=12;fontFamily=Helvetica;"
            "container=1;collapsible=0;shadow=1;strokeWidth=1.5;")
ACTOR_BASE = ("rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;fontStyle=1;fontSize=12;fontColor=#333333;"
              "fontFamily=Helvetica;container=1;collapsible=0;shadow=1;strokeWidth=1;")
EDGE_BASE = ("edgeStyle=orthogonalEdgeStyle;html=1;rounded=0;jettySize=auto;orthogonalLoop=1;"
             "strokeColor=#545B64;strokeWidth=1.5;fontFamily=Helvetica;fontSize=11;endArrow=block;endFill=1;")
# The skill uses labelBackgroundColor=none for dark mode; our exports are light only, so a white label background
# keeps labels readable where they sit on a line.
LABEL_BASE = ("edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];fontSize=11;"
              "fontFamily=Helvetica;labelBackgroundColor=#FFFFFF;")
TITLE = "text;html=1;align=left;verticalAlign=top;fontSize=28;fontStyle=1;fontFamily=Helvetica;"
SUBTITLE = "text;html=1;align=left;verticalAlign=top;fontSize=16;fontFamily=Helvetica;"
SEPARATOR = "line;strokeWidth=2;html=1;strokeColor=#FF9900;fontFamily=Helvetica;"


def icon_style(icon: IconRef) -> str:
    """resource_icon and general shapes use the resourceIcon wrapper; real sub-resources the flat shape."""
    if icon.style == "sub_resource":
        return f"{SUB_PREFIX}shape=mxgraph.aws4.{icon.shape};fillColor={icon.fill_color};"
    return f"{ICON_PREFIX}shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon.shape};fillColor={icon.fill_color};"


def group_style(boundary_type: str) -> str:
    return GROUP_STYLES.get(boundary_type, GENERIC_GROUP)
