import { Ticket } from '@/shadcn/types/tickets';
import { useEffect, useState } from 'react';

export function useTicketFilters(tickets: Ticket[] = []) {
  const [selectedPriorities, setSelectedPriorities] = useState<string[]>(() => {
    const saved = localStorage.getItem("all_selectedPriorities");
    return saved ? JSON.parse(saved) : [];
  });

  const [selectedStatuses, setSelectedStatuses] = useState<string[]>(() => {
    const saved = localStorage.getItem("all_selectedStatuses");
    return saved ? JSON.parse(saved) : [];
  });

  const [selectedAssignees, setSelectedAssignees] = useState<string[]>(() => {
    const saved = localStorage.getItem("all_selectedAssignees");
    return saved ? JSON.parse(saved) : [];
  });

  const [selectedTypes, setSelectedTypes] = useState<string[]>(() => {
    const saved = localStorage.getItem("all_selectedTypes");
    return saved ? JSON.parse(saved) : [];
  });

  useEffect(() => {
    localStorage.setItem("all_selectedPriorities", JSON.stringify(selectedPriorities));
    localStorage.setItem("all_selectedStatuses", JSON.stringify(selectedStatuses));
    localStorage.setItem("all_selectedAssignees", JSON.stringify(selectedAssignees));
    localStorage.setItem("all_selectedTypes", JSON.stringify(selectedTypes));
  }, [selectedPriorities, selectedStatuses, selectedAssignees, selectedTypes]);

  const handlePriorityToggle = (priority: string) => {
    setSelectedPriorities((prev) =>
      prev.includes(priority) ? prev.filter((p) => p !== priority) : [...prev, priority]
    );
  };

  const handleStatusToggle = (status: string) => {
    setSelectedStatuses((prev) =>
      prev.includes(status) ? prev.filter((s) => s !== status) : [...prev, status]
    );
  };

  const handleAssigneeToggle = (assignee: string) => {
    setSelectedAssignees((prev) =>
      prev.includes(assignee) ? prev.filter((a) => a !== assignee) : [...prev, assignee]
    );
  };

  const handleTypeToggle = (type: string) => {
    setSelectedTypes((prev) =>
      prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type]
    );
  };

  const clearFilters = () => {
    setSelectedPriorities([]);
    setSelectedStatuses([]);
    setSelectedAssignees([]);
    setSelectedTypes([]);
    localStorage.removeItem("all_selectedPriorities");
    localStorage.removeItem("all_selectedStatuses");
    localStorage.removeItem("all_selectedAssignees");
    localStorage.removeItem("all_selectedTypes");
  };

  const filteredTickets = tickets.filter((ticket) => {
    const priorityMatch =
      selectedPriorities.length === 0 ||
      selectedPriorities.includes(ticket.priority);
    const statusMatch =
      selectedStatuses.length === 0 ||
      selectedStatuses.includes(ticket.isComplete ? "closed" : "open");
    const assigneeMatch =
      selectedAssignees.length === 0 ||
      selectedAssignees.includes(ticket.assignedTo?.name || "Unassigned");
    const typeMatch =
      selectedTypes.length === 0 ||
      selectedTypes.includes(ticket.type);

    return priorityMatch && statusMatch && assigneeMatch && typeMatch;
  });

  return {
    selectedPriorities,
    selectedStatuses,
    selectedAssignees,
    selectedTypes,
    handlePriorityToggle,
    handleStatusToggle,
    handleAssigneeToggle,
    handleTypeToggle,
    clearFilters,
    filteredTickets
  };
}
