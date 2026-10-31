# Citation Graph Website UX Notes

## Product focus

The primary experience is a large, searchable, evidence-backed citation graph for AI and computer-science papers. The graph is the product surface; the website, REST API, and MCP are access paths to the same graph data.

The first release should make a user answer these questions quickly:

1. Which papers does this paper cite?
2. Which later papers cite it?
3. How are two papers connected through intermediate citations?
4. What is the time and topic context of the local neighborhood?
5. Where did each node and edge come from, and when was it last synchronized?

## Graph semantics

- A node represents one canonical paper record. Display the title, year, venue, authors, topic labels, DOI/arXiv/OpenAlex identifiers, and an external source link.
- A directed edge `A → B` means that paper A cites paper B. The arrow must remain visible at every zoom level where an edge is shown.
- A citation edge is a bibliographic fact. It does not by itself mean that A extends, improves, agrees with, or contradicts B. Those explanations belong to a later evidence-backed layer.
- Store and expose edge provenance: source dataset, source record ID, retrieval/snapshot date, parser status, and any unresolved target state.
- The full graph may contain cycles, cross-topic links, and older foundation papers. Tree views are navigation projections, not replacements for the directed graph.

## Scope and honesty

- Default time window: 2016-11-01 through the current data snapshot date. Show the exact date range in the filter bar.
- Older cited foundation papers may appear as external context nodes. Mark them as `outside window` instead of silently filtering them out.
- Use wording such as `indexed papers` and show coverage/update metadata. Do not present the UI as proof that every AI/CS paper has been collected.
- Distinguish `not indexed`, `metadata missing`, `preprint`, `published version`, and `retracted` states.

## Information architecture

### Header

- Product name and a one-line definition: `AI paper citation graph`.
- Global search input with title/author/identifier search.
- Snapshot badge: `Data through YYYY-MM-DD` plus indexed node/edge counts.
- Actions: reset view, share/copy query, keyboard shortcuts.

### Left control rail

- Search results list with title, year, source, and match reason.
- Filters: year range, topic/domain, source, publication status, and in-window/outside-window.
- Traversal controls: direction (`references`, `citations`, `both`), hop depth (1–3), node cap, and include external nodes.
- Layout controls: force, timeline, and topic-cluster views. Changing layout must not change the underlying edges.

### Center graph canvas

- Pan, wheel/pinch zoom, box select, node dragging, and fit-to-selection.
- Use arrows, node color, and a compact legend to encode direction, year, topic, and outside-window status.
- Render a selected node with a strong focus ring and its immediate neighborhood with an accent highlight. Fade unrelated nodes rather than deleting them.
- Show a progress indicator for large traversals and a clear `limited to N nodes / depth D` label when a cap is active.

### Right inspector

- Selected paper title and short abstract/summary.
- Authors, year, venue, identifiers, source links, and synchronization metadata.
- Two explicit lists: `References` (outgoing edges) and `Cited by` (incoming edges), with counts and lazy loading.
- For each edge, provide a source/provenance affordance and a link to the cited paper record.
- `Open in graph` buttons should focus the selected relationship without changing the global query unexpectedly.

### Bottom status strip

- Visible node and edge counts, active filters, hop depth, and render status.
- Data quality notices: unresolved targets, duplicate merges, stale snapshot, or partial source availability.

## Core interactions

1. Search a paper and press Enter. The graph centers on the canonical record and loads a bounded local subgraph.
2. Click a node to inspect metadata. The first click selects; a second click or `Open in graph` centers and expands it.
3. Click an arrow to inspect the citation relationship and its provenance. Keep the arrow direction obvious in the edge inspector.
4. Use `References` or `Cited by` buttons to expand one hop in the corresponding direction. Prevent accidental unbounded traversal.
5. Select two papers and run `Find path`. Return one or more bounded citation paths, with direction and hop count shown for every step.
6. Drag the year range or topic filters. Preserve the focal paper and show excluded neighbors as optional context rather than silently moving the user.
7. Share a view by serializing paper IDs, filters, layout, hop depth, and node cap into a URL. A shared link must reproduce the same query state against the same snapshot when possible.

## Visual and interaction rules

- Use a restrained research-tool visual language: dark canvas, high-contrast text, one accent for selection, and distinct colors for topic or status only when the legend explains them.
- Never encode citation direction by color alone; use arrowheads and accessible labels.
- Keep labels off dense graphs by default. Reveal the selected node, nearby high-degree nodes, and labels on hover/focus.
- Use motion only for camera transitions and expansion feedback. Respect `prefers-reduced-motion`.
- Keyboard support: `/` focuses search, `Escape` clears selection, arrow keys move through results, and Enter opens the focused paper. Every graph node and edge needs an accessible text alternative.

## Performance boundaries

- The default view should target about 200 visible nodes and remain responsive on a normal laptop.
- Every traversal request must carry a maximum hop depth and node/edge limit. The server and client both enforce the limit.
- Load metadata and edge lists progressively. Render the first useful neighborhood before fetching abstracts or secondary fields.
- Use viewport culling and a canvas/WebGL renderer for dense views. Keep the inspector in ordinary DOM for selectable text and accessibility.
- Debounce search and filter changes. Cache repeated paper, neighborhood, and path queries by snapshot plus query parameters.
- If a query is truncated, report the exact reason (`node cap`, `depth cap`, or `source unavailable`) and offer an explicit expand action.

## Data verification in the UI

- Every paper card should expose at least one stable identifier and a source link.
- Every edge detail should expose the source and snapshot/ingestion timestamp. If the target is unresolved, render an `unresolved target` node state.
- Show version relationships for preprint and published records when the data layer has them; do not merge versions without a recorded mapping.
- Add a `data quality` drawer with counts for duplicate candidates, missing metadata, unresolved references, and out-of-window nodes.
- Keep fact and inference labels separate. A future technical-relation explanation must show evidence, confidence, and verification state; it must not overwrite the citation edge label.

## MVP acceptance checks

- A user can search a real paper, see its local citation graph, and open the paper's references and cited-by lists.
- Direction is unambiguous on the canvas and in the inspector.
- The graph can render a bounded neighborhood of approximately 200 nodes with pan, zoom, selection, and expansion.
- At least one real source identifier and provenance field are visible for each displayed paper and relationship.
- A two-paper path query returns a bounded, stepwise path or an explicit `no path in current graph` result.
- Filters show their exact date range and whether outside-window foundation nodes are included.
- Empty, loading, partial, and error states are explicit and recoverable.

## Suggested implementation split

- `graph-data`: canonical papers, directed citation edges, provenance, and snapshot metadata.
- `query-service`: search, references, citations, subgraph, and path endpoints with depth/node caps.
- `graph-view`: rendering, layout, selection, edge inspection, and progressive expansion.
- `paper-inspector`: metadata, lists, source links, and quality/status labels.
- `share-state`: URL serialization of query and view state.

