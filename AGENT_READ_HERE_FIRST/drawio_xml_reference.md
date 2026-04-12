---

# draw.io XML Format — Complete Programmatic Reference

## 1. Document Structure

Every `.drawio` file is XML. The outer wrapper for multi-page files is `<mxfile>`, but you can embed just the inner `<mxGraphModel>` directly.

```xml
<mxfile version="21.0.0">
  <diagram name="Page-1" id="page1">
    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10"
                  guides="1" tooltips="1" connect="1" arrows="1"
                  fold="1" page="1" pageScale="1"
                  pageWidth="1169" pageHeight="827"
                  math="0" shadow="0">
      <root>
        <mxCell id="0" />                          <!-- required root -->
        <mxCell id="1" parent="0" />               <!-- required default layer -->
        <!-- all user cells go here, parent="1" by default -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

### mxGraphModel Attributes

| Attribute | Type | Meaning |
|---|---|---|
| `dx`, `dy` | int | Viewport translation offset |
| `grid` | 0/1 | Show grid |
| `gridSize` | int | Grid spacing in px (default 10) |
| `pageWidth`, `pageHeight` | int | Canvas dimensions in px (A4: 1169×827) |
| `math` | 0/1 | Enable MathJax rendering |
| `shadow` | 0/1 | Global drop shadows |

---

## 2. mxCell — The Fundamental Building Block

```xml
<mxCell id="2" value="Label Text" style="STYLE_STRING"
        vertex="1" parent="1">
  <mxGeometry x="100" y="80" width="120" height="60" as="geometry"/>
</mxCell>
```

### Core Attributes

| Attribute | Required | Notes |
|---|---|---|
| `id` | Yes | Unique string; "0" and "1" are reserved |
| `value` | No | Label; supports HTML when `html=1` |
| `style` | No | Semicolon-delimited style string |
| `vertex` | Shapes | Set to `"1"` |
| `edge` | Connections | Set to `"1"` |
| `parent` | Yes | Parent cell ID (default `"1"`) |
| `source` | Edges | Source vertex ID |
| `target` | Edges | Target vertex ID |

### ID Conventions

- `"0"` — root (no parent, always present)
- `"1"` — default layer (parent="0", always present)
- `"2"+` — user cells; use sequential integers or UUIDs
- For programmatic generation: use integers starting at 2, incrementing by 1
- For multi-layer: layer cells get IDs like `"layer-2"`, `"layer-3"` etc.

---

## 3. mxGeometry

### For vertices (shapes):
```xml
<mxGeometry x="200" y="150" width="160" height="80" as="geometry"/>
```

### For edges (connections):
```xml
<!-- Minimal — required even if empty -->
<mxGeometry relative="1" as="geometry"/>

<!-- With explicit waypoints -->
<mxGeometry relative="1" as="geometry">
  <Array as="points">
    <mxPoint x="300" y="100"/>
    <mxPoint x="300" y="200"/>
  </Array>
</mxGeometry>

<!-- With floating source/target anchors -->
<mxGeometry relative="1" as="geometry">
  <mxPoint x="100" y="100" as="sourcePoint"/>
  <mxPoint x="400" y="100" as="targetPoint"/>
</mxGeometry>
```

> **Critical**: Every edge cell MUST have `<mxGeometry relative="1" as="geometry"/>` as a child — even if it has source/target. A self-closing `<mxCell ... />` edge will not render.

---

## 4. Style String Reference

Styles are semicolon-delimited `key=value;` pairs. Order doesn't matter. The first token without `=` is often a shape type keyword (e.g., `ellipse;`, `swimlane;`).

### 4a. General Shape Properties

| Property | Values | Description |
|---|---|---|
| `rounded` | `0` / `1` | Rounded corners |
| `whiteSpace` | `wrap` / `nowrap` | Text wrapping |
| `html` | `0` / `1` | Enable HTML in label |
| `aspect` | `fixed` | Lock aspect ratio |
| `rotation` | degrees | Shape rotation |
| `opacity` | `0`–`100` | Transparency |
| `shadow` | `0` / `1` | Drop shadow |
| `glass` | `0` / `1` | Glass overlay effect |

### 4b. Color & Fill

| Property | Values | Description |
|---|---|---|
| `fillColor` | `#rrggbb` / `none` | Background fill |
| `strokeColor` | `#rrggbb` / `none` | Border color |
| `strokeWidth` | integer | Border thickness in px |
| `dashed` | `0` / `1` | Dashed border |
| `dashPattern` | e.g. `8 8` | Custom dash pattern |
| `gradientColor` | `#rrggbb` | Second color for gradient |
| `gradientDirection` | `south` / `north` / `east` / `west` | Gradient direction |

### 4c. Font & Text

| Property | Values | Description |
|---|---|---|
| `fontColor` | `#rrggbb` | Text color |
| `fontSize` | integer | Size in pts (default 11) |
| `fontFamily` | `Helvetica`, `Arial`, etc. | Typeface |
| `fontStyle` | `0`=normal, `1`=bold, `2`=italic, `4`=underline | Combinable (e.g., `3`=bold+italic) |
| `align` | `left` / `center` / `right` | Horizontal alignment |
| `verticalAlign` | `top` / `middle` / `bottom` | Vertical alignment |
| `labelPosition` | `left` / `center` / `right` | Label outside shape |
| `verticalLabelPosition` | `top` / `bottom` | Label above/below |
| `labelBackgroundColor` | `#rrggbb` / `none` | Label background |
| `spacingLeft/Right/Top/Bottom` | integer | Internal padding |

### 4d. Shape Types

**Basic shapes** — use as leading keyword or `shape=` property:

| Style | Shape |
|---|---|
| *(default / no keyword)* | Rectangle |
| `rounded=1;` | Rounded rectangle |
| `ellipse;` | Ellipse / circle |
| `rhombus;` | Diamond (decision) |
| `triangle;` | Triangle |
| `hexagon;` | Hexagon |
| `parallelogram;` | Parallelogram |
| `shape=cylinder3;` | Cylinder (database) |
| `shape=mxgraph.flowchart.document;` | Document shape |
| `shape=mxgraph.flowchart.terminator;` | Rounded ends |
| `shape=mxgraph.flowchart.manual_input;` | Manual input |
| `shape=note;` | Sticky note (folded corner) |
| `shape=mxgraph.basic.rect;` | Basic rect (for stencils) |

**Cloud/infra shapes** (stencil libraries):

| Style | Shape |
|---|---|
| `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda` | AWS Lambda |
| `shape=mxgraph.kubernetes.pod;` | Kubernetes pod |
| `shape=mxgraph.gcp2.cloud_functions;` | GCP function |

---

## 5. Edge / Connection Styles

### Edge routing styles

| `edgeStyle` value | Description |
|---|---|
| *(none)* | Direct straight line |
| `orthogonalEdgeStyle` | Right-angle (L-shape) routing |
| `elbowEdgeStyle` | Single elbow with configurable direction |
| `entityRelationEdgeStyle` | ER-diagram style (exits sides) |
| `segmentEdgeStyle` | Manually segmented |
| `isometricEdgeStyle` | Isometric diagram routing |

### Arrow tips

| Property | Common values |
|---|---|
| `startArrow` / `endArrow` | `none`, `classic`, `block`, `open`, `oval`, `diamond`, `ERmany`, `ERone`, `ERmandOne` |
| `startFill` / `endFill` | `0` (outline) / `1` (filled) |

### Edge label positioning
```xml
<!-- Inline label on edge (set value on the edge mxCell) -->
<mxCell ... edge="1" value="calls" ...>

<!-- For edge label with background -->
style="edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];"
```

### Full edge example
```xml
<mxCell id="e1" value="sends request" edge="1"
        source="node-a" target="node-b"
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;
               orthogonalLoop=1;jettySize=auto;
               exitX=1;exitY=0.5;entryX=0;entryY=0.5;
               strokeColor=#666666;strokeWidth=2;
               startArrow=none;endArrow=block;endFill=1;"
        parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

---

## 6. Swimlanes & Containers

### 6a. Simple container (invisible group)

```xml
<mxCell id="group1" value="" style="group;" vertex="1" parent="1">
  <mxGeometry x="50" y="50" width="400" height="300" as="geometry"/>
</mxCell>
<!-- Children use coordinates RELATIVE to group origin -->
<mxCell id="child1" value="Box A" style="rounded=1;whiteSpace=wrap;"
        vertex="1" parent="group1">
  <mxGeometry x="20" y="30" width="120" height="60" as="geometry"/>
</mxCell>
```

### 6b. Swimlane (labeled container with header)

```xml
<mxCell id="pool1" value="Phase 1: Ingestion"
        style="swimlane;startSize=30;fillColor=#dae8fc;
               strokeColor=#6c8ebf;fontStyle=1;fontSize=13;"
        vertex="1" parent="1">
  <mxGeometry x="60" y="60" width="900" height="200" as="geometry"/>
</mxCell>

<!-- Children parented to pool1, coords relative to pool1 -->
<mxCell id="step1" value="Fetch Data"
        style="rounded=1;whiteSpace=wrap;fillColor=#fff2cc;strokeColor=#d6b656;"
        vertex="1" parent="pool1">
  <mxGeometry x="40" y="70" width="140" height="60" as="geometry"/>
</mxCell>
```

### 6c. Horizontal swimlane (pool + lanes)

```xml
<!-- Outer pool -->
<mxCell id="pool" value="Pipeline" 
        style="shape=pool;startSize=20;horizontal=1;"
        vertex="1" parent="1">
  <mxGeometry x="80" y="100" width="800" height="400" as="geometry"/>
</mxCell>

<!-- Lane 1 — child of pool -->
<mxCell id="lane1" value="Input Layer"
        style="swimlane;startSize=30;fillColor=#f5f5f5;strokeColor=#666666;"
        vertex="1" parent="pool">
  <mxGeometry y="0" width="800" height="130" as="geometry"/>
</mxCell>

<!-- Lane 2 — child of pool -->
<mxCell id="lane2" value="Processing Layer"
        style="swimlane;startSize=30;fillColor=#e1d5e7;strokeColor=#9673a6;"
        vertex="1" parent="pool">
  <mxGeometry y="130" width="800" height="130" as="geometry"/>
</mxCell>
```

**Key swimlane style properties:**

| Property | Meaning |
|---|---|
| `startSize=30` | Header height (or width if vertical) |
| `horizontal=0` | Vertical swimlane (lanes stack left-right) |
| `childLayout=stackLayout` | Auto-stack children |
| `collapsible=0` | Disable collapse toggle |
| `swimlaneLine=1` | Show divider line between header and body |

---

## 7. Layers

Layers are `mxCell` elements with `parent="0"` (root). Shapes reference a layer's ID as their parent.

```xml
<root>
  <mxCell id="0"/>
  <mxCell id="1" parent="0"/>              <!-- default layer -->
  <mxCell id="layer-schema" value="Schema Layer" parent="0" visible="1"/>
  <mxCell id="layer-api"    value="API Layer"    parent="0" visible="1"/>

  <!-- shapes on schema layer -->
  <mxCell id="s1" value="Users Table" ... parent="layer-schema" vertex="1">
    <mxGeometry x="100" y="100" width="160" height="60" as="geometry"/>
  </mxCell>

  <!-- shapes on api layer -->
  <mxCell id="a1" value="POST /users" ... parent="layer-api" vertex="1">
    <mxGeometry x="400" y="100" width="160" height="60" as="geometry"/>
  </mxCell>
</root>
```

---

## 8. Color Palette Recommendations

### Semantic color themes (fillColor → strokeColor pairs)

| Zone | Fill | Stroke | Use for |
|---|---|---|---|
| Blue (info) | `#dae8fc` | `#6c8ebf` | Input, data sources |
| Yellow (process) | `#fff2cc` | `#d6b656` | Computation, transform |
| Green (success) | `#d5e8d4` | `#82b366` | Output, success state |
| Purple (external) | `#e1d5e7` | `#9673a6` | External services, APIs |
| Red (error/alert) | `#f8cecc` | `#b85450` | Errors, alerts |
| Grey (infra) | `#f5f5f5` | `#666666` | Infrastructure, background |
| Dark header | `#1e1e2e` | `#1e1e2e` | Dark zone headers |

---

## 9. Complete Working Example

A small pipeline diagram with containers, shapes, edges, and a legend:

```xml
<mxfile version="21.0.0">
  <diagram name="Pipeline" id="main">
    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10"
                  pageWidth="1169" pageHeight="827">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>

        <!-- ═══ PHASE 1 SWIMLANE ═══ -->
        <mxCell id="phase1" value="Phase 1 — Ingestion"
                style="swimlane;startSize=32;fillColor=#dae8fc;
                       strokeColor=#6c8ebf;fontStyle=1;fontSize=13;
                       fontColor=#0050a0;"
                vertex="1" parent="1">
          <mxGeometry x="60" y="60" width="500" height="160" as="geometry"/>
        </mxCell>

        <!-- Nodes inside Phase 1 (coords relative to swimlane) -->
        <mxCell id="n1" value="API Source"
                style="rounded=1;whiteSpace=wrap;html=1;
                       fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="phase1">
          <mxGeometry x="30" y="60" width="120" height="60" as="geometry"/>
        </mxCell>

        <mxCell id="n2" value="Queue"
                style="shape=mxgraph.flowchart.sequential_data;
                       whiteSpace=wrap;html=1;
                       fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="phase1">
          <mxGeometry x="200" y="60" width="120" height="60" as="geometry"/>
        </mxCell>

        <mxCell id="n3" value="Parser"
                style="rounded=1;whiteSpace=wrap;html=1;
                       fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="phase1">
          <mxGeometry x="360" y="60" width="100" height="60" as="geometry"/>
        </mxCell>

        <!-- Edge inside Phase 1 -->
        <mxCell id="e1" value="" edge="1" source="n1" target="n2"
                style="edgeStyle=orthogonalEdgeStyle;rounded=0;
                       strokeColor=#6c8ebf;strokeWidth=2;
                       endArrow=block;endFill=1;"
                parent="phase1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e2" value="" edge="1" source="n2" target="n3"
                style="edgeStyle=orthogonalEdgeStyle;rounded=0;
                       strokeColor=#6c8ebf;strokeWidth=2;
                       endArrow=block;endFill=1;"
                parent="phase1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- ═══ PHASE 2 SWIMLANE ═══ -->
        <mxCell id="phase2" value="Phase 2 — Processing"
                style="swimlane;startSize=32;fillColor=#d5e8d4;
                       strokeColor=#82b366;fontStyle=1;fontSize=13;
                       fontColor=#3a6b35;"
                vertex="1" parent="1">
          <mxGeometry x="60" y="270" width="500" height="160" as="geometry"/>
        </mxCell>

        <mxCell id="n4" value="Transform"
                style="rounded=1;whiteSpace=wrap;html=1;
                       fillColor=#d5e8d4;strokeColor=#82b366;"
                vertex="1" parent="phase2">
          <mxGeometry x="60" y="60" width="140" height="60" as="geometry"/>
        </mxCell>

        <mxCell id="n5" value="Validate"
                style="rhombus;whiteSpace=wrap;html=1;
                       fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="phase2">
          <mxGeometry x="270" y="45" width="140" height="80" as="geometry"/>
        </mxCell>

        <mxCell id="e3" value="" edge="1" source="n4" target="n5"
                style="edgeStyle=orthogonalEdgeStyle;strokeColor=#82b366;
                       strokeWidth=2;endArrow=block;endFill=1;"
                parent="phase2">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- ═══ CROSS-PHASE EDGE (parent="1") ═══ -->
        <!-- When connecting nodes across containers, parent must be "1"
             and source/target IDs must be globally unique -->
        <mxCell id="cross1" value="parsed records" edge="1"
                source="n3" target="n4"
                style="edgeStyle=orthogonalEdgeStyle;curved=1;
                       strokeColor=#666;strokeWidth=2;dashed=1;
                       dashPattern=8 4;endArrow=open;endFill=0;"
                parent="1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- ═══ LEGEND (invisible group container) ═══ -->
        <mxCell id="legend" value="" style="group;" vertex="1" parent="1">
          <mxGeometry x="620" y="60" width="200" height="180" as="geometry"/>
        </mxCell>

        <mxCell id="leg-title" value="Legend"
                style="text;html=1;fontStyle=1;fontSize=13;align=center;
                       verticalAlign=middle;"
                vertex="1" parent="legend">
          <mxGeometry x="0" y="0" width="200" height="30" as="geometry"/>
        </mxCell>

        <mxCell id="leg-1" value=""
                style="rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="legend">
          <mxGeometry x="10" y="40" width="30" height="20" as="geometry"/>
        </mxCell>
        <mxCell id="leg-1t" value="Processing Step"
                style="text;html=1;align=left;verticalAlign=middle;"
                vertex="1" parent="legend">
          <mxGeometry x="50" y="40" width="140" height="20" as="geometry"/>
        </mxCell>

        <mxCell id="leg-2" value=""
                style="rhombus;fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="legend">
          <mxGeometry x="10" y="70" width="30" height="25" as="geometry"/>
        </mxCell>
        <mxCell id="leg-2t" value="Decision"
                style="text;html=1;align=left;verticalAlign=middle;"
                vertex="1" parent="legend">
          <mxGeometry x="50" y="70" width="140" height="25" as="geometry"/>
        </mxCell>

      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

---

## 10. Cross-Container Edges — Critical Rule

When an edge connects nodes in **different** containers, the edge's `parent` must be `"1"` (the canvas layer), **not** either container. The `source` and `target` still reference the global cell IDs.

```xml
<!-- n3 is inside phase1, n4 is inside phase2 -->
<mxCell id="cross" edge="1" source="n3" target="n4"
        parent="1"     <!-- ← must be "1", not phase1 or phase2 -->
        style="edgeStyle=orthogonalEdgeStyle;...">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

---

## 11. Tips for Generating Large Diagrams Programmatically

### Layout strategy
- **Grid your zones**: allocate rectangular regions (e.g., 900px wide per zone column)
- **Phase rows**: stack swimlanes vertically with `y` offset = cumulative heights + gap (e.g., 20px)
- **Node spacing**: minimum 60px between nodes; 200px horizontal preferred
- **Swimlane `startSize`**: account for it — content starts at `y = startSize` within the swimlane

### ID generation
```python
# Simple counter approach
cell_id = 2
def next_id():
    global cell_id
    cell_id += 1
    return str(cell_id)
```

### Geometry helper
```python
def node(id, label, x, y, w=140, h=60, style="rounded=1;whiteSpace=wrap;html=1;", parent="1"):
    return f'<mxCell id="{id}" value="{label}" style="{style}" vertex="1" parent="{parent}">' \
           f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>' \
           f'</mxCell>'

def edge(id, src, tgt, label="", style="edgeStyle=orthogonalEdgeStyle;", parent="1"):
    return f'<mxCell id="{id}" value="{label}" edge="1" source="{src}" target="{tgt}" ' \
           f'style="{style}" parent="{parent}">' \
           f'<mxGeometry relative="1" as="geometry"/>' \
           f'</mxCell>'
```

### Multi-section diagram pattern
```
Canvas layout:
┌──────────────────────────────────────────────────────────┐
│  [Zone A Swimlane]   [Zone B Swimlane]   [Legend group]  │
│  y=60, x=60          y=60, x=620         y=60, x=1000   │
│                                                          │
│  [Zone C Swimlane spanning full width]                   │
│  y=300, x=60, width=950                                  │
└──────────────────────────────────────────────────────────┘
```

### Character escaping in labels
- `&` → `&amp;`
- `<` → `&lt;`  
- `>` → `&gt;`
- `"` in style strings → avoid; use single-quoted values or omit quotes

### HTML labels
With `html=1;` in style, `value` can contain HTML:
```xml
value="&lt;b&gt;Title&lt;/b&gt;&lt;br/&gt;subtitle"
```

### Gradient syntax
```
fillColor=#dae8fc;gradientColor=#6c8ebf;gradientDirection=south;
```

---

## 12. Quick Shape Style Cheatsheet

```
Rectangle (default):    whiteSpace=wrap;html=1;
Rounded rect:           rounded=1;arcSize=10;whiteSpace=wrap;html=1;
Circle:                 ellipse;whiteSpace=wrap;html=1;aspect=fixed;
Diamond:                rhombus;whiteSpace=wrap;html=1;
Cylinder/DB:            shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;
Document:               shape=mxgraph.flowchart.document;whiteSpace=wrap;html=1;
Swimlane:               swimlane;startSize=30;
Invisible container:    group;
Text only:              text;html=1;align=center;verticalAlign=middle;resizable=0;
Image:                  shape=image;verticalLabelPosition=bottom;aspect=fixed;image=...;
```

---

`★ Insight ─────────────────────────────────────`
- The **two-cell bootstrap** (`id="0"` and `id="1"`) is mandatory — draw.io will silently fail or crash on import without them.
- Children of swimlanes use **local coordinates** — a node at `x=40,y=70` inside a swimlane that sits at `x=60,y=300` on the canvas will render at canvas position `(100, 362)` (accounting for `startSize=32`).
- Cross-container edges must be parented to `"1"` — if you parent them to a container, they disappear when the container moves.
`─────────────────────────────────────────────────`

---

**Sources:**
- [File Format Reference — DeepWiki/jgraph drawio-diagrams](https://deepwiki.com/jgraph/drawio-diagrams/10-file-format-reference)
- [draw.io Style Reference for AI File Generation](https://www.drawio.com/doc/faq/drawio-style-reference)
- [drawio-mcp XML Reference — jgraph/drawio-mcp GitHub](https://github.com/jgraph/drawio-mcp/blob/main/shared/xml-reference.md)
- [Working with Swimlanes in draw.io](https://drawio-app.com/blog/working-with-swimlanes-in-draw-io/)
- [Manually edit the XML source — draw.io docs](https://www.drawio.com/doc/faq/diagram-source-edit)