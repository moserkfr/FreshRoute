import BottomSheet from "../landing/BottomSheet";
import Hero from "../landing/Hero";
import HowItWorks from "../landing/HowItWorks";
import Reviews from "../landing/Reviews";
import StatTiles from "../landing/StatTiles";
import "../landing/landing.css";

export default function Landing() {
  return (
    <>
      <Hero />
      <StatTiles />
      <HowItWorks />
      <Reviews />
      <BottomSheet />
    </>
  );
}
