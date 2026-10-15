import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Review } from "../api/types";
import { ErrorNotice, PillButton } from "../components/ui";

const TONES = ["amber", "yellow", "peri", "pink"];
const ART = [
  "linear-gradient(160deg, #f6b24a, #e2541f 55%, #5b2a12)",
  "linear-gradient(160deg, #b9d39a, #6c8f6a 50%, #2a3b28)",
  "linear-gradient(160deg, #e6d2b7, #9a7b65 50%, #3f2d26)",
  "linear-gradient(160deg, #f0c3d8, #c0584f 55%, #5a1f1f)",
];

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}

function Stars({ n, size = 16 }: { n: number; size?: number }) {
  return (
    <span className="stars" aria-label={`${n} out of 5`}>
      {[1, 2, 3, 4, 5].map((k) => (
        <svg key={k} width={size} height={size} viewBox="0 0 20 20" aria-hidden>
          <path d="M10 1.8l2.5 5.3 5.7.7-4.2 3.9 1.1 5.7L10 14.6l-5.1 2.8 1.1-5.7L1.8 7.8l5.7-.7z" fill={k <= n ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        </svg>
      ))}
    </span>
  );
}

export default function Reviews() {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", role: "", rating: 0, text: "" });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [thanks, setThanks] = useState(false);

  useEffect(() => {
    api.reviews().then(setReviews).catch((e) => { setReviews([]); setLoadError((e as Error).message); });
  }, []);

  const canSubmit = form.name.trim() && form.rating > 0 && form.text.trim().length >= 10;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) {
      setFormError("Add your name, a star rating and at least 10 characters of review.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const saved = await api.addReview({ ...form, name: form.name.trim(), role: form.role.trim(), text: form.text.trim() });
      setReviews((r) => [saved, ...(r ?? [])]);
      setForm({ name: "", role: "", rating: 0, text: "" });
      setThanks(true);
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="section reviews" id="reviews">
      <div className="wrap">
        <h2 className="h2">what people say <em>after a dispatch</em></h2>
        <p className="lede center" style={{ textAlign: "center" }}>
          Have you run FreshRoute on your own scenarios? Tell us how the plan held up. Every review
          below was written by someone who used it.
        </p>

        <div className="review-layout">
          <form className="card review-form" onSubmit={submit} noValidate>
            <h3 className="h3">give your <em>review</em></h3>
            <div className="review-form-row">
              <label className="field">Name
                <input className="input" maxLength={60} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="name" />
              </label>
              <label className="field">Role <span className="muted">(optional)</span>
                <input className="input" maxLength={80} placeholder="e.g. hub operations lead" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} />
              </label>
            </div>
            <fieldset className="field rating-field">
              <legend>Rating</legend>
              <div className="rating-pick" role="radiogroup">
                {[1, 2, 3, 4, 5].map((k) => (
                  <button key={k} type="button" role="radio" aria-checked={form.rating === k} aria-label={`${k} star${k > 1 ? "s" : ""}`}
                    className={k <= form.rating ? "on" : ""} onClick={() => setForm({ ...form, rating: k })}>
                    <svg width="22" height="22" viewBox="0 0 20 20" aria-hidden><path d="M10 1.8l2.5 5.3 5.7.7-4.2 3.9 1.1 5.7L10 14.6l-5.1 2.8 1.1-5.7L1.8 7.8l5.7-.7z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="field">Review
              <textarea className="textarea" maxLength={1000} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })}
                placeholder="What did the plan get right, and what would you change?" />
              <span className="muted num" style={{ justifySelf: "end", fontSize: 12 }}>{form.text.length}/1000</span>
            </label>
            {formError && <ErrorNotice message={formError} onClose={() => setFormError(null)} />}
            {thanks && !formError && <div className="notice info" role="status">Thanks. Your review is published below.</div>}
            <div><PillButton type="submit" tone="amber" busy={saving}>Publish review</PillButton></div>
          </form>

          <div className="review-list" aria-live="polite">
            {reviews === null && <div className="review-empty"><span className="spinner" /> Loading reviews…</div>}
            {reviews !== null && reviews.length === 0 && (
              <div className="review-empty">
                <div className="review-empty-mark" aria-hidden>“</div>
                <p className="h3">no reviews <em>yet</em></p>
                <p className="muted">
                  {loadError ? `Reviews are unavailable: ${loadError}` : "Yours will be the first. Use the form to share how the plan worked for you."}
                </p>
              </div>
            )}
            {reviews?.map((r, n) => (
              <article key={r.id} className={`review-row block ${TONES[n % TONES.length]}`}>
                <div className="review-art" style={{ background: ART[n % ART.length] }}>
                  <span>{initials(r.name)}</span>
                </div>
                <div className="review-body">
                  <Stars n={r.rating} />
                  <p className="review-quote">“{r.text}”</p>
                  <div className="review-by">
                    <strong>{r.name}</strong>
                    {r.role && <span>{r.role}</span>}
                    <span className="num">{new Date(r.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
