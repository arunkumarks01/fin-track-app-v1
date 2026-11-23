import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Learn · FinTrack" };

const lessons = [
  {
    title: "Backtest",
    description: "A simulation of a trading idea on historical data.",
    example:
      "A backtest can show how a rule behaved in an earlier period. It cannot tell you what will happen next.",
    check:
      "Look for the dates, instruments, entry and exit rules, data quality, and treatment of costs.",
  },
  {
    title: "Drawdown",
    description: "The fall in account value from an earlier peak.",
    example: "If equity falls from ₹1,00,000 to ₹90,000, the drawdown from that peak is 10%.",
    check:
      "Look at when it occurred, how long recovery took, and whether open positions were included.",
  },
  {
    title: "Win rate",
    description: "The percentage of completed trades that made a profit.",
    example:
      "Six profitable trades out of ten is a 60% win rate. It does not tell you the size of the wins or losses.",
    check: "Compare average gains, average losses, trading costs and the number of trades too.",
  },
  {
    title: "Slippage",
    description: "The difference between a modeled or expected price and the actual fill price.",
    example:
      "A buy expected at ₹100 but filled at ₹100.10 has ₹0.10 of adverse slippage per share.",
    check: "A backtest must state its fill assumptions; a real order may fill differently.",
  },
  {
    title: "Overfitting",
    description: "Tuning a strategy so closely to past data that it may not work on unseen data.",
    example:
      "Selecting the best of many parameter combinations on the same history can make chance look like skill.",
    check:
      "Review results on a later, untouched period and consider the number of variations tested.",
  },
];

export default function LearnPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-4 sm:p-6">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Learn the language of results</h1>
        <p className="text-muted-foreground">
          Short explanations to help you question a backtest before trusting its numbers.
        </p>
      </header>
      <section aria-labelledby="terms-title" className="overflow-hidden rounded-xl border bg-card">
        <div className="border-b p-5">
          <h2 id="terms-title" className="text-lg font-semibold">
            Five useful concepts
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Open a term for an example and a question to ask.
          </p>
        </div>
        {lessons.map((lesson) => (
          <details key={lesson.title} className="group border-b last:border-0">
            <summary className="cursor-pointer px-5 py-4 font-medium focus-visible:outline-2 focus-visible:outline-offset-[-3px] focus-visible:outline-ring">
              {lesson.title}
              <span className="ml-2 font-normal text-muted-foreground">— {lesson.description}</span>
            </summary>
            <div className="space-y-3 px-5 pb-5 text-sm leading-relaxed">
              <p>{lesson.example}</p>
              <p className="text-muted-foreground">
                <strong className="text-foreground">What to check:</strong> {lesson.check}
              </p>
            </div>
          </details>
        ))}
      </section>
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold">See the numbers in context</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Follow an invented four-trade example to see how return, win rate and drawdown fit
          together.
        </p>
        <Link
          href="/backtest"
          className="mt-3 inline-block font-medium text-primary underline underline-offset-4"
        >
          Open the backtest example
        </Link>
      </div>
    </main>
  );
}
