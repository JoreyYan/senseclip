import { useRef, useEffect, useCallback } from "react";
import cytoscape, { Core, EventObject } from "cytoscape";
import type { Person, CoRelation, DirectedEdge, DirectedNode } from "@/types";
import { CATEGORY_COLORS, RELATION_EDGE_STYLES } from "@/constants";

interface NetworkGraphProps {
  persons: Person[];
  relations: CoRelation[];
  directedNodes?: DirectedNode[];
  directedEdges?: DirectedEdge[];
  isEgoMode: boolean;
  egoPersonId?: string;
  onNodeClick: (personId: string, personName: string) => void;
  onNodeHover?: (personId: string | null) => void;
  searchTarget?: string | null;
  categoryFilter: string;
  relationFilter: string;
  minMention?: number;
}

function buildElements(
  persons: Person[],
  relations: CoRelation[],
  directedNodes: DirectedNode[],
  directedEdges: DirectedEdge[],
  isEgoMode: boolean,
  egoPersonId: string | undefined,
  categoryFilter: string,
  relationFilter: string,
  minMention: number = 1
) {
  const nodes: any[] = [];
  const edges: any[] = [];

  if (isEgoMode && directedNodes.length > 0) {
    // Ego mode: use directed graph data
    const filteredNodes = categoryFilter === "all"
      ? directedNodes
      : directedNodes.filter((n) => n.category === categoryFilter);

    const nodeIds = new Set(filteredNodes.map((n) => n.id));

    filteredNodes.forEach((n) => {
      const isCenter = n.id === egoPersonId;
      nodes.push({
        data: {
          id: n.id,
          label: n.name,
          category: n.category,
          mentionCount: n.mention_count,
          isCenter,
          size: isCenter ? 60 : Math.max(20, Math.min(45, 15 + n.mention_count * 0.8)),
        },
      });
    });

    directedEdges.forEach((e) => {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return;
      if (relationFilter !== "all" && e.relation_type !== relationFilter) return;
      edges.push({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          relationType: e.relation_type,
          description: e.description,
          confidence: e.confidence,
          width: Math.max(1, e.confidence * 3),
          directed: true,
        },
      });
    });
  } else {
    // Global mode: use co-occurrence data
    const filteredPersons = persons.filter((p) =>
      (categoryFilter === "all" || p.category === categoryFilter) &&
      p.mention_count >= minMention
    );

    const personIds = new Set(filteredPersons.map((p) => p.person_id));
    const maxMention = Math.max(...filteredPersons.map((p) => p.mention_count), 1);

    filteredPersons.forEach((p) => {
      nodes.push({
        data: {
          id: p.person_id,
          label: p.name,
          category: p.category,
          mentionCount: p.mention_count,
          videoCount: p.video_count,
          isCenter: false,
          size: Math.max(25, Math.min(70, 20 + (p.mention_count / maxMention) * 50)),
        },
      });
    });

    const maxStrength = Math.max(...relations.map((r) => r.strength), 1);
    relations.forEach((r, i) => {
      if (!personIds.has(r.source_person_id) || !personIds.has(r.target_person_id)) return;
      if (relationFilter !== "all" && r.relation_type !== relationFilter) return;
      edges.push({
        data: {
          id: `rel_${i}`,
          source: r.source_person_id,
          target: r.target_person_id,
          relationType: r.relation_type,
          strength: r.strength,
          width: Math.max(0.5, (r.strength / maxStrength) * 4),
          directed: false,
        },
      });
    });
  }

  return [...nodes.map((n) => ({ group: "nodes" as const, ...n })), ...edges.map((e) => ({ group: "edges" as const, ...e }))];
}

export function NetworkGraph({
  persons,
  relations,
  directedNodes = [],
  directedEdges = [],
  isEgoMode,
  egoPersonId,
  onNodeClick,
  searchTarget,
  categoryFilter,
  relationFilter,
  minMention = 1,
}: NetworkGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  const initGraph = useCallback(() => {
    if (!containerRef.current) return;
    if (cyRef.current) cyRef.current.destroy();

    const elements = buildElements(
      persons, relations, directedNodes, directedEdges,
      isEgoMode, egoPersonId, categoryFilter, relationFilter, minMention
    );

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            width: "data(size)",
            height: "data(size)",
            "background-color": (ele: any) => CATEGORY_COLORS[ele.data("category")] || "#6b7280",
            "border-width": (ele: any) => (ele.data("videoCount") >= 3 || ele.data("isCenter")) ? 3 : 1,
            "border-color": (ele: any) => {
              if (ele.data("isCenter")) return "#f97316";
              if (ele.data("videoCount") >= 3) return "#fbbf24";
              return "rgba(0,0,0,0.08)";
            },
            "border-opacity": 1,
            color: "#1e293b",
            "font-size": 12,
            "text-margin-y": 5,
            "text-valign": "bottom",
            "text-outline-color": "#faf8f5",
            "text-outline-width": 2,
            "min-zoomed-font-size": 6,
            "overlay-opacity": 0,
          } as any,
        },
        {
          selector: "node[?isCenter]",
          style: {
            "background-opacity": 1,
            "border-width": 4,
            "border-color": "#f97316",
            "font-size": 16,
            "font-weight": "bold" as any,
            "z-index": 999,
          } as any,
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": (ele: any) => RELATION_EDGE_STYLES[ele.data("relationType")]?.color || "#4b5563",
            "line-style": (ele: any) => (RELATION_EDGE_STYLES[ele.data("relationType")]?.style === "dashed" ? "dashed" : "solid") as any,
            "curve-style": "bezier",
            opacity: 0.5,
            "target-arrow-shape": (ele: any) => ele.data("directed") ? "triangle" : "none",
            "target-arrow-color": (ele: any) => RELATION_EDGE_STYLES[ele.data("relationType")]?.color || "#4b5563",
            "arrow-scale": 0.8,
          } as any,
        },
        {
          selector: "edge[description]",
          style: {
            label: "",
            "font-size": 10,
            color: "#64748b",
            "text-rotation": "autorotate" as any,
            "text-outline-color": "#faf8f5",
            "text-outline-width": 1.5,
          } as any,
        },
        {
          selector: ".highlighted",
          style: { opacity: 1 } as any,
        },
        {
          selector: ".faded",
          style: { opacity: 0.08 } as any,
        },
      ],
      layout: isEgoMode
        ? { name: "concentric", concentric: (n: any) => n.data("isCenter") ? 10 : 1, levelWidth: () => 1, minNodeSpacing: 60 }
        : { name: "cose", idealEdgeLength: (edge: any) => { const s = edge.source().data("size"); const t = edge.target().data("size"); return 120 + (s + t) * 1.5; }, nodeOverlap: 80, refresh: 20, fit: false, padding: 20, randomize: true, componentSpacing: 80, nodeRepulsion: (node: any) => { const d = node.degree(false); return d > 15 ? 800000 : d > 8 ? 300000 : 120000; }, gravity: 0.08, numIter: 3000, animate: false },
      minZoom: 0.15,
      maxZoom: 5,
      wheelSensitivity: 0.3,
    });

    // Hover interaction
    cy.on("mouseover", "node", (e: EventObject) => {
      const node = e.target;
      const neighborhood = node.closedNeighborhood();
      cy.elements().addClass("faded");
      neighborhood.removeClass("faded").addClass("highlighted");
      // Show edge labels on connected edges (set each individually, not with function)
      neighborhood.edges().forEach((edge: any) => {
        const desc = edge.data("description");
        if (desc) edge.style("label", desc);
      });
      containerRef.current!.style.cursor = "pointer";
    });

    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("faded").removeClass("highlighted");
      cy.edges().forEach((edge: any) => edge.style("label", ""));
      containerRef.current!.style.cursor = "default";
    });

    // Click interaction
    cy.on("tap", "node", (e: EventObject) => {
      const node = e.target;
      onNodeClick(node.data("id"), node.data("label"));
    });

    // Click background to deselect
    cy.on("tap", (e: EventObject) => {
      if (e.target === cy) {
        cy.elements().removeClass("faded").removeClass("highlighted");
      }
    });

    // After layout, zoom to show the densest area at readable size
    cy.one("layoutstop", () => {
      // Find the node with highest degree (most connections) as the center of interest
      let centerNode = cy.nodes().max((n: any) => n.degree(false)).ele;
      if (!centerNode || centerNode.length === 0) centerNode = cy.nodes().first();
      if (centerNode && centerNode.length > 0) {
        cy.zoom({ level: 1.0, position: centerNode.position() });
        cy.center(centerNode);
      }
    });

    cyRef.current = cy;
  }, [persons, relations, directedNodes, directedEdges, isEgoMode, egoPersonId, categoryFilter, relationFilter, minMention, onNodeClick]);

  useEffect(() => {
    initGraph();
    return () => {
      if (cyRef.current) cyRef.current.destroy();
    };
  }, [initGraph]);

  // Search: find and center on node
  useEffect(() => {
    if (!searchTarget || !cyRef.current) return;
    const cy = cyRef.current;
    const node = cy.nodes().filter((n: any) => n.data("label").includes(searchTarget));
    if (node.length > 0) {
      cy.animate({ center: { eles: node.first() }, zoom: 2 }, { duration: 500 });
      const neighborhood = node.first().closedNeighborhood();
      cy.elements().addClass("faded");
      neighborhood.removeClass("faded").addClass("highlighted");
      setTimeout(() => {
        cy.elements().removeClass("faded").removeClass("highlighted");
      }, 3000);
    }
  }, [searchTarget]);

  // Prevent parent scroll container from stealing wheel — cytoscape reads deltaY for zoom regardless
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => { e.preventDefault(); };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  return (
    <div
      ref={containerRef}
      className="w-full h-full"
      style={{ backgroundColor: "hsl(var(--background))" }}
    />
  );
}
