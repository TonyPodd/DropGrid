import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { AccountsPage, CommunitiesPage, DashboardPage } from "./catalog";
import { GridDetailPage, GridImportPage, GridsPage } from "./grids";
import { MediaPage } from "./media";
import {
  CampaignDetailPage,
  CampaignNewPage,
  CampaignsPage,
} from "./campaigns";
const VkAuthHelper =
  import.meta.env.VITE_ENABLE_VK_AUTH_HELPER === "true"
    ? lazy(() => import("./dev/VkAuthHelper"))
    : null;
export function App() {
  return (
    <div className="app-shell">
      <aside>
        <NavLink className="brand" to="/">
          DropGrid<span>Campaign workspace</span>
        </NavLink>
        <nav aria-label="Main navigation">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/campaigns">Campaigns</NavLink>
          <NavLink to="/grids">Grids</NavLink>
          <NavLink to="/communities">Communities</NavLink>
          <NavLink to="/accounts">Accounts</NavLink>
          <NavLink to="/media">Media</NavLink>
        </nav>
        <p className="sidebar-note">
          Preparation mode
          <br />
          VK sending is disabled
        </p>
      </aside>
      <main>
        <Routes>
          {VkAuthHelper &&
            ["/dev/vk-auth", "/dev/vk-auth/copy"].map((path) => (
              <Route
                key={path}
                path={path}
                element={
                  <Suspense fallback={<p>Loading helper…</p>}>
                    <VkAuthHelper />
                  </Suspense>
                }
              />
            ))}
          <Route path="/" element={<DashboardPage />} />
          <Route path="/grids" element={<GridsPage />} />
          <Route path="/grids/new" element={<GridImportPage />} />
          <Route path="/grids/:id" element={<GridDetailPage />} />
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/campaigns/new" element={<CampaignNewPage />} />
          <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/communities" element={<CommunitiesPage />} />
          <Route path="/media" element={<MediaPage />} />
          <Route
            path="*"
            element={
              <>
                <h1>Страница не найдена</h1>
                <NavLink to="/">Dashboard</NavLink>
              </>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
