/**
 * v5.292.0 — several scanner issues about ONE product on the same ports, as
 * one line ("Apache Tomcat 9.0.13 · 14 issues").
 *
 * Nessus checks an outdated product once per advisory range, so a single old
 * Tomcat filled the host's Scanner observations with a dozen critical rows.
 * The grouping is by the scanner's CPE (utils/vulnGrouping `groupByProduct`),
 * never by title. Opening the line shows every issue exactly as before — each
 * keeps its own finding, promote and dismiss; nothing is merged underneath.
 */
import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Package } from 'lucide-react';

import type { ProductGroup } from '../../utils/vulnGrouping';
import { Badge } from '../ui/badge';
import { VulnerabilityGroup, type VulnerabilityGroupProps } from './VulnerabilityGroup';

export interface ProductObservationGroupProps extends Omit<VulnerabilityGroupProps, 'group' | 'defaultOpen'> {
  product: ProductGroup;
  /** Host id, so an issue's open state never carries across hosts. */
  hostId: number;
}

export const ProductObservationGroup: React.FC<ProductObservationGroupProps> = ({
  product, hostId, ...rowProps
}) => {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const name = product.installedVersion ? `${product.product} ${product.installedVersion}` : product.product;
  const issueCount = product.groups.length;
  const source = product.sources.length > 1
    ? `${product.sources.length} scanners`
    : (product.sources[0] ?? 'unknown').toUpperCase();

  return (
    <div className="border-b border-border pb-xs last:border-b-0 last:pb-0" data-testid="product-observation-group">
      <div className="flex min-w-0 items-center gap-xs">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`${name}: ${issueCount} issues from one product`}
          className="flex min-w-0 flex-1 items-center gap-xs rounded py-xxs text-left hover:bg-accent/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Chevron className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <Badge
            variant={rowProps.severityBadgeVariant(product.severity) as never}
            className="w-[5.5rem] shrink-0 justify-center"
          >
            {product.severity.toUpperCase()}
          </Badge>
          <Package className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <h4 className="min-w-0 truncate text-metadata font-medium text-foreground" title={name}>
            {name}
          </h4>
          <span className="shrink-0 text-caption text-muted-foreground">· {issueCount} issues</span>
        </button>

        <p className="max-w-[40%] shrink-0 truncate text-caption text-muted-foreground">
          {source}
          {product.ports.length > 0 && (
            <> · Port{product.ports.length > 1 ? 's' : ''} {product.ports.join(', ')}</>
          )}
          {product.closingVersion && <> · fixed in {product.closingVersion}</>}
        </p>
        {product.exploitableCount > 0 && (
          <Badge
            variant="destructive"
            className="shrink-0"
            title={`${product.exploitableCount} of these issues have a known exploit`}
          >
            {product.exploitableCount} Exploit
          </Badge>
        )}
      </div>

      {open && (
        <div className="min-w-0 space-y-xs pb-xs pl-[1.75rem] pt-xxs">
          <p className="text-caption text-muted-foreground">
            {product.closingVersion
              ? `Every check here names a fixed version; ${product.product} ${product.closingVersion} or later satisfies all of them. `
              : ''}
            Each issue below is still judged on its own.
          </p>
          {product.groups.map((group) => (
            <VulnerabilityGroup key={`${hostId}:${group.key}`} group={group} {...rowProps} />
          ))}
        </div>
      )}
    </div>
  );
};

export default ProductObservationGroup;
