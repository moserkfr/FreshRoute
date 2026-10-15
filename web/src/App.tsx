import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import Footer from "./components/Footer";
import Nav from "./components/Nav";
import Evaluation from "./pages/Evaluation";
import Landing from "./pages/Landing";
import MapPage from "./pages/MapPage";
import MarketPulse from "./pages/MarketPulse";
import NotFound from "./pages/NotFound";
import Settings from "./pages/Settings";
import Sources from "./pages/Sources";
import TodayPlan from "./pages/TodayPlan";
import MyStock from "./pages/MyStock";
import WhatIf from "./pages/WhatIf";

export default function App() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/plan" element={<TodayPlan />} />
          <Route path="/stock" element={<MyStock />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/market" element={<MarketPulse />} />
          <Route path="/whatif" element={<WhatIf />} />
          <Route path="/evaluation" element={<Evaluation />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </>
  );
}
