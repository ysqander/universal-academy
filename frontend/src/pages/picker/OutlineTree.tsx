import type { MouseEvent } from "react";
import { Icon } from "../../components/Icon";
import { pageRangeLabel } from "../../lib/format";
import type { OutlineNode } from "../../types";

interface OutlineTreeProps {
  nodes: OutlineNode[];
  expanded: Set<string>;
  onToggle: (id: string) => void;
  /** `extend` = shift-click: grow the selection contiguously to this node. */
  onSelect: (id: string, extend: boolean) => void;
  range: { start: number; end: number } | null;
  endpoints: Set<string>;
}

export function OutlineTree(props: OutlineTreeProps) {
  return (
    <ul className="outline" aria-label="Table of contents">
      {props.nodes.map((node) => (
        <OutlineRow key={node.id} node={node} depth={0} {...props} />
      ))}
    </ul>
  );
}

function OutlineRow({ node, depth, ...props }: OutlineTreeProps & { node: OutlineNode; depth: number }) {
  const { expanded, onToggle, onSelect, range, endpoints } = props;
  const open = expanded.has(node.id);
  const hasChildren = node.children.length > 0;
  const inside = range !== null && node.start_page >= range.start && node.end_page <= range.end;
  const partial = !inside && range !== null && node.start_page <= range.end && node.end_page >= range.start;
  const pages = node.end_page - node.start_page + 1;

  const select = (event: MouseEvent<HTMLButtonElement>) => onSelect(node.id, event.shiftKey);

  return (
    <li className="outline__item">
      <div
        className="outline__row"
        data-depth={Math.min(depth, 3)}
        data-selected={inside || undefined}
        data-partial={partial || undefined}
        data-endpoint={endpoints.has(node.id) || undefined}
        style={{ paddingLeft: `calc(${depth} * 18px + 4px)` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="outline__toggle"
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} ${node.title}`}
            onClick={() => onToggle(node.id)}
          >
            <Icon name={open ? "chevronDown" : "chevronRight"} size={16} />
          </button>
        ) : (
          <span className="outline__toggle outline__toggle--leaf" aria-hidden="true" />
        )}
        <button
          type="button"
          className="outline__select"
          aria-pressed={inside}
          onClick={select}
          title="Click to select · Shift-click to extend the selection"
        >
          <span className="outline__title">{node.title}</span>
          <span className="outline__pages" title={`${pages} ${pages === 1 ? "page" : "pages"}`}>
            {pageRangeLabel(node.label_start, node.label_end)}
          </span>
        </button>
      </div>
      {hasChildren && open && (
        <ul className="outline__children">
          {node.children.map((child) => (
            <OutlineRow key={child.id} node={child} depth={depth + 1} {...props} />
          ))}
        </ul>
      )}
    </li>
  );
}
