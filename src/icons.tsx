import {
  Bath,
  Bed,
  Car,
  Flower2,
  Home,
  Sofa,
  Trees,
  Utensils,
  WashingMachine,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

export const ROOM_ICONS: Record<string, { icon: LucideIcon; label: string }> = {
  home: { icon: Home, label: 'Home' },
  utensils: { icon: Utensils, label: 'Kitchen' },
  bath: { icon: Bath, label: 'Bathroom' },
  bed: { icon: Bed, label: 'Bedroom' },
  sofa: { icon: Sofa, label: 'Living room' },
  'washing-machine': { icon: WashingMachine, label: 'Laundry' },
  car: { icon: Car, label: 'Garage' },
  wrench: { icon: Wrench, label: 'Utility' },
  trees: { icon: Trees, label: 'Yard' },
  flower: { icon: Flower2, label: 'Garden' },
}

export function RoomIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ROOM_ICONS[name]?.icon ?? Home
  return <Icon className={className} />
}
