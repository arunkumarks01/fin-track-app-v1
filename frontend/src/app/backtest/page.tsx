import type { Metadata } from "next";
import { BacktestResults } from "./results";

export const metadata: Metadata = { title: "Historical studies · FinTrack" };

export default function BacktestPage() {
  return <BacktestResults />;
}
