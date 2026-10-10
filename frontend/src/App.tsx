import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, roleHome, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import RequireRole from "./components/RequireRole";
import Health from "./pages/Health";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Tools from "./pages/Tools";
import AdminDashboard from "./pages/admin/Dashboard";
import Advertisers from "./pages/admin/Advertisers";
import CampaignsAdmin from "./pages/admin/Campaigns";
import Publishers from "./pages/admin/Publishers";
import CreativeModeration from "./pages/admin/CreativeModeration";
import AdvertiserDashboard from "./pages/advertiser/Dashboard";
import CampaignBuilder from "./pages/advertiser/CampaignBuilder";
import CampaignDetail from "./pages/advertiser/CampaignDetail";
import NewCampaign from "./pages/advertiser/NewCampaign";
import AdvertiserDomains from "./pages/advertiser/Domains";
import PublisherDashboard from "./pages/publisher/Dashboard";
import PublisherProperties from "./pages/publisher/Properties";
import PublisherAdUnits from "./pages/publisher/AdUnits";
import PublisherApiKeys from "./pages/publisher/ApiKeys";
import PublisherWebhooks from "./pages/publisher/Webhooks";
import Integration from "./pages/publisher/Integration";
import AdPreview from "./pages/publisher/AdPreview";
 function HomeRedirect() { const { session } = useAuth(); return <Navigate to={session ? roleHome(session.user.role) : "/login"} replace />; }
export default function App() { return <AuthProvider><BrowserRouter><Routes><Route path="/login" element={<Login />} /><Route path="/signup" element={<Signup />} /><Route element={<RequireRole />}><Route element={<Layout />}><Route index element={<HomeRedirect />} /><Route path="/admin" element={<RequireRole roles={["admin"]} />}><Route index element={<AdminDashboard />} /><Route path="advertisers" element={<Advertisers />} /><Route path="campaigns" element={<CampaignsAdmin />} /><Route path="creatives" element={<CreativeModeration />} /><Route path="publishers" element={<Publishers />} /></Route><Route path="/advertiser" element={<RequireRole roles={["advertiser"]} />}><Route index element={<AdvertiserDashboard />} /><Route path="domains" element={<AdvertiserDomains />} /><Route path="campaign-builder" element={<CampaignBuilder />} /><Route path="campaigns/new" element={<NewCampaign />} /><Route path="campaigns/:id" element={<CampaignDetail />} /></Route><Route path="/publisher" element={<RequireRole roles={["publisher"]} />}><Route index element={<PublisherDashboard />} /><Route path="properties" element={<PublisherProperties />} /><Route path="slots" element={<PublisherAdUnits />} /><Route path="keys" element={<PublisherApiKeys />} /><Route path="webhooks" element={<PublisherWebhooks />} /><Route path="integration" element={<Integration />} /><Route path="preview" element={<AdPreview />} /></Route><Route path="/tools" element={<Tools />} /><Route path="/health" element={<Health />} /></Route></Route><Route path="*" element={<Navigate to="/" replace />} /></Routes></BrowserRouter></AuthProvider>; }
