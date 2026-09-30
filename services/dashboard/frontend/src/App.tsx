import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import LoginPage from "./auth/LoginPage";
import Layout from "./components/Layout";
import { LiveSocketProvider } from "./hooks/useLiveSocket";
import AlertsPage from "./pages/AlertsPage";
import FleetOverview from "./pages/FleetOverview";
import ObservabilityPage from "./pages/ObservabilityPage";
import RiskPage from "./pages/RiskPage";
import SimulationControlPage from "./pages/SimulationControlPage";
import SqlSandboxPage from "./pages/SqlSandboxPage";
import VehicleDetail from "./pages/VehicleDetail";
import VehicleSearch from "./pages/VehicleSearch";

function RequireAuth({ children }: { children: ReactNode }) {
  const { auth } = useAuth();
  const location = useLocation();
  if (!auth) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <LiveSocketProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<FleetOverview />} />
            <Route path="vehicles" element={<VehicleSearch />} />
            <Route path="vehicles/:vehicleId" element={<VehicleDetail />} />
            <Route path="alerts" element={<AlertsPage />} />
            <Route path="risk" element={<RiskPage />} />
            <Route path="simulation" element={<SimulationControlPage />} />
            <Route path="observability" element={<ObservabilityPage />} />
            <Route path="sql" element={<SqlSandboxPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </LiveSocketProvider>
    </AuthProvider>
  );
}
