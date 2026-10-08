import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PublicationCheckButton, SubmissionTable } from "./campaigns";
import { campaignsApi } from "./api/campaigns";
import type { Submission } from "./api/types";

const item: Submission = {
  id: "s1", community: { id: "c1", domain: "club242100737", name: "Test", category: null,
    vk_group_id: 242100737, is_active: true }, category: null, account_name: "Test",
  media_label: null, status: "submitted", attempt_count: 1, error_code: null,
  error_message: null, published_post_url: null,
};

it("shows moderation, publication time/link and not-found states", () => {
  render(<SubmissionTable items={[
    item, { ...item, id: "s2", status: "published", published_at: "2026-10-08T18:16:06Z",
      published_post_url: "https://vk.com/wall-242100737_5" },
    { ...item, id: "s3", status: "not_found" },
  ]} />);
  expect(screen.getByText("На модерации")).toBeInTheDocument();
  expect(screen.getByText("Опубликовано")).toBeInTheDocument();
  expect(screen.getByText("Не найдено")).toBeInTheDocument();
  expect(screen.getByRole("link")).toHaveAttribute("href", "https://vk.com/wall-242100737_5");
  expect(screen.getByText(/2026/)).toBeInTheDocument();
});

it("checks only on click and keeps temporary read errors separate from publication failure", async () => {
  const check = vi.spyOn(campaignsApi, "checkPublication").mockResolvedValue({
    current_status: "submitted", evidence: { result: "read_error" },
  });
  const reload = vi.fn();
  render(<PublicationCheckButton id="s1" onChecked={reload} />);
  expect(check).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button"));
  expect(check).toHaveBeenCalledWith("s1");
  expect(reload).toHaveBeenCalledOnce();
  expect(screen.getByRole("status")).toHaveTextContent("Статус публикации не изменён");
  expect(screen.queryByText("FAILED")).not.toBeInTheDocument();
});

it("handles unavailable manual checks without showing raw error details", async () => {
  vi.spyOn(campaignsApi, "checkPublication").mockRejectedValue(new Error("private-sentinel"));
  render(<PublicationCheckButton id="s1" onChecked={vi.fn()} />);
  await userEvent.click(screen.getByRole("button"));
  expect(screen.getByRole("status")).toHaveTextContent("Попробуйте позже");
  expect(document.body).not.toHaveTextContent("private-sentinel");
});
