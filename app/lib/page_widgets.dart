// Shared visual components for native status pages.
import 'package:flutter/material.dart';

/// A titled block of a native page (recorder, upload).
Widget sectionCard(BuildContext context, String title, Widget child) {
  final t = Theme.of(context), cs = t.colorScheme;
  return Card.filled(
      margin: const EdgeInsets.only(top: 12),
      color: cs.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: t.textTheme.labelLarge?.copyWith(color: cs.onSurfaceVariant)),
          const SizedBox(height: 10),
          child,
        ]),
      ));
}

/// One number with its icon, a line under it and, given a fraction, a bar (battery, card, queue).
Widget statTile(BuildContext context,
    {required IconData icon, required Color color, required String label, required String value, String? sub, double? fraction}) {
  final t = Theme.of(context), cs = t.colorScheme;
  return Expanded(
    child: Card.filled(
      margin: EdgeInsets.zero,
      color: cs.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(icon, color: color, size: 22),
            const SizedBox(width: 6),
            Flexible(child: Text(label, style: t.textTheme.labelLarge?.copyWith(color: cs.onSurfaceVariant), overflow: TextOverflow.ellipsis)),
          ]),
          const SizedBox(height: 10),
          // a narrow tile (three in a row) shrinks a long value rather than wrapping it
          FittedBox(fit: BoxFit.scaleDown, alignment: AlignmentDirectional.centerStart,
              child: Text(value, maxLines: 1, style: t.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w600))),
          if (sub != null) Text(sub, style: t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
          if (fraction != null) ...[
            const SizedBox(height: 10),
            LinearProgressIndicator(value: fraction, color: color, backgroundColor: color.withValues(alpha: 0.15),
                minHeight: 6, borderRadius: BorderRadius.circular(3)),
          ],
        ]),
      ),
    ),
  );
}

/// The header of a native page: an icon, a name, and chips saying what is going on.
Widget heroCard(BuildContext context, {required IconData icon, required String title, required List<Widget> chips}) {
  final t = Theme.of(context), cs = t.colorScheme;
  return Card.filled(
    margin: const EdgeInsets.only(top: 8),
    color: cs.primaryContainer,
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Row(children: [
        CircleAvatar(radius: 28, backgroundColor: cs.primary, child: Icon(icon, color: cs.onPrimary, size: 30)),
        const SizedBox(width: 16),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: t.textTheme.titleLarge?.copyWith(color: cs.onPrimaryContainer, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Wrap(spacing: 8, runSpacing: 6, children: chips),
          ]),
        ),
      ]),
    ),
  );
}

/// A status chip in one colour.
Widget stateChip(String label, Color color, IconData icon) => Chip(
      avatar: Icon(icon, size: 18, color: color),
      label: Text(label),
      visualDensity: VisualDensity.compact,
      side: BorderSide(color: color.withValues(alpha: 0.5)),
    );
