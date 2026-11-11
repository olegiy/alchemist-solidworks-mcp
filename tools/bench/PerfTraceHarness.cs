using System;
using System.Collections.Generic;
using SolidworksExecution.Infrastructure;

internal static class PerfTraceHarness
{
    private static int Main()
    {
        var rows = new List<string>();
        long ticks = 10;
        using (PerfTrace.Measure("body", "op", true, () => ticks, rows.Add))
            ticks = 30;
        if (rows.Count != 1 || !rows[0].Contains("\"duration_ticks\":20")) return 1;
        using (PerfTrace.Measure("body", "op", false, () => ticks, rows.Add))
            ticks = 50;
        if (rows.Count != 1) return 2;
        using (PerfTrace.Measure("private/path", "op", true, () => ticks, rows.Add))
            ticks = 60;
        if (rows.Count != 1) return 3;
        using (PerfTrace.Measure("body", "op", true, () => ticks, row => { throw new InvalidOperationException(); }))
            ticks = 70;
        using (PerfTrace.Measure("body", "private/path", true, () => ticks, rows.Add))
            ticks = 80;
        if (rows.Count != 1) return 4;
        var scope = PerfTrace.Measure("body", "op", true, () => ticks, rows.Add);
        ticks = 90;
        scope.Dispose();
        scope.Dispose();
        if (rows.Count != 2) return 5;
        return 0;
    }
}
