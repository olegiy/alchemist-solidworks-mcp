using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;

namespace SolidworksExecution.Infrastructure
{
    public static class PerfTrace
    {
        private static readonly object Gate = new object();
        private static readonly HashSet<string> Names = new HashSet<string>
        { "queue", "attach", "before_snapshot", "body", "rebuild", "actual_readback", "after_tree", "sketch_status", "serialization", "logging" };

        public static IDisposable Measure(string name, string operationId)
        {
            return Measure(name, operationId, Environment.GetEnvironmentVariable("SWMCP_PERF") == "1",
                Stopwatch.GetTimestamp, Write);
        }

        public static IDisposable Measure(string name, string operationId, bool enabled, Func<long> clock, Action<string> sink)
        {
            if (!enabled || !Names.Contains(name) || operationId == null ||
                !Regex.IsMatch(operationId, "^[A-Za-z0-9_-]{1,64}$")) return new Scope(null, null, null, null);
            return new Scope(name, operationId, clock, sink);
        }

        private static void Write(string row)
        {
            var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SolidWorksMCP", "perf.jsonl");
            lock (Gate)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.AppendAllText(path, row + Environment.NewLine);
            }
        }

        private sealed class Scope : IDisposable
        {
            private readonly string name, operationId;
            private readonly Func<long> clock;
            private readonly Action<string> sink;
            private readonly long started;
            private bool disposed;

            public Scope(string name, string operationId, Func<long> clock, Action<string> sink)
            {
                this.name = name;
                this.operationId = operationId;
                this.clock = clock;
                this.sink = sink;
                if (clock != null) started = clock();
            }

            public void Dispose()
            {
                if (disposed || clock == null) return;
                disposed = true;
                try
                {
                    var duration = Math.Max(0, clock() - started);
                    sink("{\"operation_id\":\"" + operationId + "\",\"span\":\"" + name +
                        "\",\"duration_ticks\":" + duration.ToString(CultureInfo.InvariantCulture) +
                        ",\"frequency\":" + Stopwatch.Frequency.ToString(CultureInfo.InvariantCulture) + "}");
                }
                catch { }
            }
        }
    }
}
