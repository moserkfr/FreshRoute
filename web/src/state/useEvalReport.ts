import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EvalReport } from "../api/types";
import sample from "../../sample/eval_latest.json";

/** /eval/latest, falling back to the bundled sample report (fresh clones have no reports/). */
export function useEvalReport() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [isSample, setIsSample] = useState(false);
  useEffect(() => {
    let live = true;
    api.evalLatest()
      .then((r) => { if (live) { setReport(r); setIsSample(false); } })
      .catch(() => { if (live) { setReport(sample as unknown as EvalReport); setIsSample(true); } });
    return () => { live = false; };
  }, []);
  return { report, isSample };
}
